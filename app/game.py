"""Game state machine: step ordering, the generate-vs-edit rule, and I/O glue.

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

# Action names returned by decide_action.
ACTION_GENERATE = "generate"  # text-to-image: create the base image
ACTION_EDIT = "edit"          # image-to-image: edit the current image
ACTION_CARRY = "carry"        # empty prompt: keep the current image unchanged


# --------------------------------------------------------------------------- #
# Pure state helpers (no I/O — safe to unit test directly)
# --------------------------------------------------------------------------- #
def latest_image_url(game: dict) -> Optional[str]:
    """Return the most recent non-null step image URL, or None if still blank.

    Steps run in order, so scanning steps 3→1 yields the latest image actually
    produced. This is the "current on-screen image" players describe and edit.

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
    """Decide whether the incoming prompt generates, edits, or is a no-op.

    Encodes the spec's rules:
      * Empty prompt -> carry the current image forward (step forfeited).
      * Non-empty prompt with no base image yet -> generate (text-to-image).
      * Non-empty prompt with a base image present -> edit (image-to-image).

    Args:
        game: The current game row dict.
        prompt: The submitted prompt text (possibly empty/whitespace).

    Returns:
        One of :data:`ACTION_GENERATE`, :data:`ACTION_EDIT`, :data:`ACTION_CARRY`.
    """
    if not prompt or not prompt.strip():
        return ACTION_CARRY
    # A base image exists once any prior step produced one.
    return ACTION_EDIT if latest_image_url(game) else ACTION_GENERATE


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
def create_new_game(group_name: str, group_size: int) -> dict:
    """Create a game session and assign it a reference from the pool.

    Args:
        group_name: The group's chosen name.
        group_size: Number of players; clamped to the supported range 3..4.

    Returns:
        The newly created game row dict (including its ``id``).
    """
    # Clamp defensively so a hand-edited form can't create an out-of-range game.
    size = 4 if int(group_size) >= 4 else 3
    reference = storage.assign_reference()
    return storage.create_game(group_name.strip() or "Unnamed group", size, reference.id)


def submit_prompt(game_id: str, step: int, prompt: str) -> dict:
    """Apply one step's prompt: generate/edit/carry, persist, and maybe finish.

    Args:
        game_id: The game's id.
        step: The step being submitted (1..3).
        prompt: The submitted prompt text (may be empty on a timed-out turn).

    Returns:
        The updated game row dict. After step 3 it also carries the final scores.

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
    provider = build_image_provider()

    if action == ACTION_GENERATE:
        image_bytes = provider.generate(prompt.strip())
        new_url = storage.upload_generated_image(game_id, step, image_bytes)
    elif action == ACTION_EDIT:
        # Fetch the current on-screen image and edit it into the next one.
        current_bytes = storage.fetch_image_bytes(latest_image_url(game))
        image_bytes = provider.edit(current_bytes, prompt.strip())
        new_url = storage.upload_generated_image(game_id, step, image_bytes)
    else:  # ACTION_CARRY — empty prompt: keep the current image (possibly None)
        new_url = latest_image_url(game)

    # Record this step's prompt + resulting image and advance the pointer.
    updates = {
        f"prompt_{step}": prompt.strip(),
        f"image_url_{step}": new_url,
        "current_step": step,
    }
    game = storage.update_game(game_id, updates)

    # After the final step, score the result and publish to the leaderboard.
    if step == TOTAL_STEPS:
        game = scoring.finalize_game(game, latest_image_url(game))
    return game
