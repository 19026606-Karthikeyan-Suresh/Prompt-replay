"""Game state machine: step ordering, the regenerate-every-turn rule, and I/O glue.

The pure decision helpers at the top (``latest_image_url``, ``decide_action``,
``player_label`` …) contain the actual game rules and take/return plain data, so
they are unit-testable with no Supabase or AI provider. The orchestration
functions at the bottom (``create_new_game``, ``submit_prompt``) wire those rules
to the storage layer, the image provider, and scoring.
"""

from __future__ import annotations

from typing import Optional

from . import scoring, storage
from .config import build_image_provider

# A relay is always exactly three steps (spec: "There are always 3 steps total").
TOTAL_STEPS = 3

# Action names returned by decide_action. Every non-empty prompt regenerates a
# fresh image (broken-telephone: each player re-describes what they see and the
# AI redraws from scratch), so there is no image-to-image "edit" action.
ACTION_GENERATE = "generate"  # text-to-image: draw a fresh image from the prompt
ACTION_CARRY = "carry"        # empty prompt: keep the previous image unchanged


# --------------------------------------------------------------------------- #
# Pure state helpers (no I/O — safe to unit test directly)
# --------------------------------------------------------------------------- #
def latest_image_url(game: dict) -> Optional[str]:
    """Return the most recent non-null step image URL, or None if still blank.

    Steps run in order, so scanning steps 3→1 yields the latest image actually
    produced — the previous player's image that the next player describes.

    Args:
        game: A game row dict with ``image_url_1..3`` keys.

    Returns:
        The latest non-null image URL, or ``None`` if no step has produced an
        image yet (base canvas still blank).
    """
    for step in (3, 2, 1):
        url = game.get(f"image_url_{step}")
        if url:
            return url
    return None


def decide_action(game: dict, prompt: str) -> str:
    """Decide whether the incoming prompt draws a fresh image or is a no-op.

    Broken-telephone rule: every non-empty prompt regenerates a brand-new image
    from the player's description (text-to-image) — the previous image is only
    shown for the player to describe, never carried forward as pixels. An empty
    prompt forfeits the turn and keeps the previous image.

      * Empty/whitespace prompt -> carry the previous image forward (forfeit).
      * Any non-empty prompt     -> generate a fresh image (text-to-image).

    Args:
        game: The current game row dict.
        prompt: The submitted prompt text (possibly empty/whitespace).

    Returns:
        :data:`ACTION_GENERATE` for a non-empty prompt, else :data:`ACTION_CARRY`.
    """
    if not prompt or not prompt.strip():
        return ACTION_CARRY
    return ACTION_GENERATE


def player_label(group_size: int, step: int) -> str:
    """Human-readable label for who takes a given step.

    Steps 1 and 2 are always solo. Step 3 is solo for a 3-person group, or a pair
    (players 3 & 4) for a 4-person group.

    Args:
        group_size: Number of players (3 or 4).
        step: The step number (1..3).

    Returns:
        A label such as "Player 2" or "Players 3 & 4 (pair)".
    """
    if step == TOTAL_STEPS and group_size >= 4:
        return "Players 3 & 4 (pair)"
    return f"Player {step}"


def next_step(game: dict) -> int:
    """The step number the group should play next (1..3), or 0 if finished.

    Args:
        game: The current game row dict.

    Returns:
        ``current_step + 1`` while steps remain, else ``0`` when all 3 are done.
    """
    current = int(game.get("current_step", 0))
    return current + 1 if current < TOTAL_STEPS else 0


# --------------------------------------------------------------------------- #
# Orchestration (touches storage + providers + scoring)
# --------------------------------------------------------------------------- #
def create_new_game(group_name: str, group_size: int, group_id: str = "") -> dict:
    """Create a game session and assign it a reference from the pool.

    Args:
        group_name: The group's chosen name.
        group_size: Number of players; clamped to the supported range 3..4.
        group_id: The event group identifier the participants entered (may be "").

    Returns:
        The newly created game row dict (including its ``id``).
    """
    # Clamp defensively so a hand-edited form can't create an out-of-range game.
    size = 4 if int(group_size) >= 4 else 3
    reference = storage.assign_reference()
    return storage.create_game(
        group_name.strip() or "Unnamed group", size, reference.id, group_id.strip()
    )


def submit_prompt(game_id: str, step: int, prompt: str) -> dict:
    """Apply one step's prompt: generate or carry, then persist and advance.

    Scoring is intentionally NOT done here. After step 3 the relay is complete but
    unscored (``current_step == TOTAL_STEPS``, ``finished`` still False); the reveal
    page scores it lazily via :func:`ensure_scored`. This keeps every request to a
    single AI call so none of them exceeds a serverless function timeout.

    Args:
        game_id: The game's id.
        step: The step being submitted (1..3).
        prompt: The submitted prompt text (may be empty on a timed-out turn).

    Returns:
        The updated game row dict.

    Raises:
        ValueError: If the game does not exist, or the step is out of range.
    """
    game = storage.get_game(game_id)
    if game is None:
        raise ValueError(f"Game {game_id} not found.")
    if step < 1 or step > TOTAL_STEPS:
        raise ValueError(f"Step {step} is out of range (1..{TOTAL_STEPS}).")

    # Idempotency: if this step (or an earlier one) was already recorded — e.g. a
    # duplicate submit from the timer firing alongside a manual click — just
    # return the current state instead of re-generating an image.
    if step <= int(game.get("current_step", 0)):
        return game

    action = decide_action(game, prompt)

    if action == ACTION_GENERATE:
        # Redraw a fresh image from this player's description of what they saw.
        provider = build_image_provider()
        image_bytes = provider.generate(prompt.strip())
        new_url = storage.upload_generated_image(game_id, step, image_bytes)
    else:  # ACTION_CARRY — empty prompt: keep the previous image (possibly None)
        new_url = latest_image_url(game)

    # Record this step's prompt + resulting image and advance the pointer.
    updates = {
        f"prompt_{step}": prompt.strip(),
        f"image_url_{step}": new_url,
        "current_step": step,
    }
    # Scoring happens later on the reveal page (see ensure_scored), not here.
    return storage.update_game(game_id, updates)


def ensure_scored(game: dict) -> dict:
    """Score a completed-but-unscored game (lazy finalize on the reveal page).

    Idempotent: if the game is already ``finished`` (scored + on the leaderboard),
    it is returned unchanged. Otherwise it runs the AI judge, persists the scores,
    and appends the leaderboard row. Running this here (on the reveal GET) rather
    than inside the step-3 POST keeps each request to a single AI call.

    Args:
        game: The game row dict; expected to have all 3 steps recorded.

    Returns:
        The scored game row dict (``finished`` True).
    """
    if game.get("finished"):
        return game
    return scoring.finalize_game(game, latest_image_url(game))
