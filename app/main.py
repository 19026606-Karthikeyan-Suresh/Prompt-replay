"""FastAPI application: all Prompt Relay HTTP routes.

Thin controllers only — game rules live in :mod:`app.game`, persistence in
:mod:`app.storage`, and scoring in :mod:`app.scoring`. Routes render Jinja2
templates and issue redirects to move the relay from one screen to the next.
"""

from __future__ import annotations

import os

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import game, storage
from .config import get_settings
from .providers.base import AllProvidersFailed
from .storage import StorageNotConfigured

# Project root = parent of this app/ package; templates/ and static/ sit there.
_BASE_DIR = os.path.dirname(os.path.dirname(__file__))

app = FastAPI(title="Prompt Relay")
app.mount("/static", StaticFiles(directory=os.path.join(_BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(_BASE_DIR, "templates"))


def _error_page(request: Request, message: str, retry_url: str, status_code: int = 500) -> HTMLResponse:
    """Render the shared error page.

    Args:
        request: The incoming request (required by Jinja2Templates).
        message: Facilitator-facing explanation of what went wrong.
        retry_url: Where the "try again" button should point.
        status_code: HTTP status to return.

    Returns:
        The rendered error page response.
    """
    return templates.TemplateResponse(
        request,
        "error.html",
        {"message": message, "retry_url": retry_url},
        status_code=status_code,
    )


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    """Render the group-name entry screen.

    Args:
        request: The incoming request.

    Returns:
        The rendered index page.
    """
    return templates.TemplateResponse(request, "index.html", {})


@app.post("/game")
def create_game(group_name: str = Form(...), group_size: int = Form(3)):
    """Create a game, assign a reference, and redirect straight to step 1.

    Args:
        group_name: The group's chosen name (from the form).
        group_size: Number of players, 3 or 4 (from the form).

    Returns:
        A redirect to the step 1 round screen, or an error page if Supabase
        or the reference pool is not ready.
    """
    try:
        new_game = game.create_new_game(group_name, group_size)
    except (StorageNotConfigured, RuntimeError) as exc:
        # Rendered without a Request-bound template here would fail; build inline.
        return HTMLResponse(f"<h1>Setup incomplete</h1><p>{exc}</p>", status_code=500)
    # There is no separate reveal/memorise screen: Player 1 sees the target on
    # their own Step 1 round screen, so jump straight into Step 1.
    return RedirectResponse(url=f"/game/{new_game['id']}/round/1", status_code=303)


@app.get("/game/{game_id}/round/{n}", response_class=HTMLResponse)
def round_screen(request: Request, game_id: str, n: int) -> HTMLResponse:
    """Show the canvas, timer, and prompt box for step ``n``.

    Redirects to the correct step (or the reveal) if ``n`` is not the step the
    group should currently be playing, so refreshes/back-buttons stay coherent.

    Args:
        request: The incoming request.
        game_id: The game's id.
        n: The requested step number.

    Returns:
        The round page, or a redirect/error as appropriate.
    """
    current = storage.get_game(game_id)
    if current is None:
        return _error_page(request, "That game was not found.", "/", status_code=404)
    # All 3 steps played -> the reveal (which scores lazily if not yet scored).
    # next_step == 0 covers both the scored (finished) and not-yet-scored states.
    if game.next_step(current) == 0:
        return RedirectResponse(url=f"/game/{game_id}/reveal", status_code=303)

    expected = game.next_step(current)  # the step the group must play now
    if n != expected:
        return RedirectResponse(url=f"/game/{game_id}/round/{expected}", status_code=303)

    # Broken-telephone visibility: only Player 1 (step 1) sees the target. Later
    # players see just the previous player's image, so the target URL must never
    # reach their page at all — only load/pass it on step 1. (Cheap on step 1: the
    # pool is lru_cached and the public URL is cached after the first upload.)
    reference_image_url = None
    if n == 1:
        reference = storage.get_reference(current["reference_id"])
        reference_image_url = storage.ensure_reference_uploaded(reference)

    settings = get_settings()
    response = templates.TemplateResponse(
        request,
        "round.html",
        {
            "game": current,
            "step": n,
            "total_steps": game.TOTAL_STEPS,
            "player": game.player_label(current["group_size"], n),
            "reference_image_url": reference_image_url,
            "current_image_url": game.latest_image_url(current),
            "seconds": settings.prompt_seconds,
            "post_url": f"/game/{game_id}/round/{n}",
        },
    )
    # Prevent the browser back button from resurfacing a prior step (e.g. Player 1's
    # target) from the bfcache after the device is passed to the next player.
    response.headers["Cache-Control"] = "no-store"
    return response


@app.post("/game/{game_id}/round/{n}")
def submit_round(request: Request, game_id: str, n: int, prompt: str = Form("")):
    """Apply a step's prompt (generate/carry) and advance the relay.

    Args:
        request: The incoming request.
        game_id: The game's id.
        n: The step being submitted.
        prompt: The prompt text; empty on a timed-out, unfilled turn.

    Returns:
        A redirect to the next round or the reveal, or an error page if all AI
        providers failed (so the facilitator can retry the step).
    """
    try:
        updated = game.submit_prompt(game_id, n, prompt)
    except AllProvidersFailed as exc:
        # Every provider failed — let the facilitator retry this exact step.
        return _error_page(
            request,
            f"Image generation failed for this step ({exc}). You can retry.",
            f"/game/{game_id}/round/{n}",
            status_code=502,
        )
    except (StorageNotConfigured, ValueError) as exc:
        return _error_page(request, str(exc), "/", status_code=400)

    # All 3 steps done -> reveal (it scores lazily). next_step == 0 means finished
    # playing; scoring itself now happens on the reveal GET, not in this POST.
    if game.next_step(updated) == 0:
        return RedirectResponse(url=f"/game/{game_id}/reveal", status_code=303)
    return RedirectResponse(url=f"/game/{game_id}/round/{n + 1}", status_code=303)


@app.get("/game/{game_id}/reveal", response_class=HTMLResponse)
def reveal(request: Request, game_id: str) -> HTMLResponse:
    """Show the reference, its details, the full relay, and the final score.

    Args:
        request: The incoming request.
        game_id: The game's id.

    Returns:
        The reveal page, a redirect if the group still owes a step, or a retryable
        error page if scoring failed (a refresh re-runs it).
    """
    current = storage.get_game(game_id)
    if current is None:
        return _error_page(request, "That game was not found.", "/", status_code=404)
    if game.next_step(current) != 0:
        # Not all 3 steps played yet — send the group to the step they still owe.
        return RedirectResponse(
            url=f"/game/{game_id}/round/{game.next_step(current)}", status_code=303
        )

    # All steps done: score lazily here (the heavy AI judge calls run in this GET,
    # not in the step-3 POST). Idempotent — a no-op once already scored.
    try:
        current = game.ensure_scored(current)
    except AllProvidersFailed as exc:
        return _error_page(
            request,
            f"Scoring failed ({exc}). Refresh to try again.",
            f"/game/{game_id}/reveal",
            status_code=502,
        )

    reference = storage.get_reference(current["reference_id"])
    reference_image_url = storage.ensure_reference_uploaded(reference)

    # Assemble the relay progression: one entry per step with its player + output.
    steps = []
    for i in range(1, game.TOTAL_STEPS + 1):
        steps.append(
            {
                "step": i,
                "player": game.player_label(current["group_size"], i),
                "prompt": current.get(f"prompt_{i}") or "(no prompt — turn forfeited)",
                "image_url": current.get(f"image_url_{i}"),
            }
        )

    # judge_result is stored as JSON; verdicts drive the per-detail checklist.
    judge_result = current.get("judge_result") or {"verdicts": []}
    similarity_pct = round(float(current.get("similarity") or 0) * 100)

    return templates.TemplateResponse(
        request,
        "reveal.html",
        {
            "game": current,
            "reference_image_url": reference_image_url,
            "details": reference.details,
            "steps": steps,
            "verdicts": judge_result.get("verdicts", []),
            "detail_score": current.get("detail_score"),
            "similarity_pct": similarity_pct,
            "final_image_url": game.latest_image_url(current),
        },
    )


# Medal glyph + label per podium tier (index 0 = top score). Mirrors MEDALS /
# LABELS in static/js/leaderboard.js — keep the two in sync.
_PODIUM_MEDALS = [("🥇", "Gold"), ("🥈", "Silver"), ("🥉", "Bronze")]


def _tier_key(row: dict) -> tuple:
    """Score key that defines a medal tier: detail score + displayed similarity %.

    Similarity is keyed on its rounded percentage — the value players actually
    see — so two groups both shown as "75%" share a tier even if their raw floats
    differ by a hair. Mirrors ``tierKey()`` in static/js/leaderboard.js.

    Args:
        row: A leaderboard row.

    Returns:
        A hashable key; rows with equal keys belong to the same medal tier.
    """
    return (row["detail_score"], round(float(row["similarity"] or 0) * 100))


def top_tiers(rows: list, count: int = 3) -> list:
    """Group rank-ordered leaderboard rows into up to ``count`` medal tiers.

    Consecutive rows sharing a :func:`_tier_key` form one tier, so every group
    tied at a score is featured together (a tier with 2+ groups is a tie). ``rows``
    must already be in rank order — as returned by
    :func:`storage.list_leaderboard` — which keeps equal-key rows contiguous, so a
    single linear pass suffices. Mirrors ``topTiers()`` in
    static/js/leaderboard.js.

    Args:
        rows: Leaderboard rows in rank order.
        count: Maximum number of medal tiers to build (default 3).

    Returns:
        A list of tier dicts: ``{"medal", "label", "groups": [row, ...]}``.
    """
    tiers: list = []
    for row in rows:
        key = _tier_key(row)
        if tiers and tiers[-1]["key"] == key:
            tiers[-1]["groups"].append(row)
        elif len(tiers) < count:
            medal, label = _PODIUM_MEDALS[len(tiers)]
            tiers.append({"key": key, "medal": medal, "label": label, "groups": [row]})
        else:
            break
    return tiers


@app.get("/leaderboard", response_class=HTMLResponse)
def leaderboard(request: Request) -> HTMLResponse:
    """Render the ranked leaderboard and wire up live realtime updates.

    The anon key + Supabase URL are passed to the page so the browser can
    subscribe to leaderboard inserts directly (read-only) via realtime.

    Args:
        request: The incoming request.

    Returns:
        The leaderboard page.
    """
    settings = get_settings()
    try:
        rows = storage.list_leaderboard()
    except StorageNotConfigured as exc:
        return _error_page(request, str(exc), "/", status_code=500)

    return templates.TemplateResponse(
        request,
        "leaderboard.html",
        {
            "rows": rows,
            # Top-3 medal tiers (ties featured) for the winners podium.
            "podium_tiers": top_tiers(rows),
            # Public, read-only credentials — safe to embed in the page.
            "supabase_url": settings.supabase_url,
            "supabase_anon_key": settings.supabase_anon_key,
        },
    )
