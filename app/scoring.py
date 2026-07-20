"""Scoring orchestration: detail judging + similarity, then persistence.

:func:`compute_scores` is the pure core — give it the final image bytes, the
reference image bytes, the 10 details, and a judge, and it returns the numbers.
It takes an injectable judge so it can be unit-tested with the keyless mock and
no Supabase. :func:`finalize_game` wraps it with the I/O: fetching images,
writing the scores back to the game row, and appending to the leaderboard.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from . import storage
from .config import build_judge
from .providers.base import DetailJudge, DetailVerdict, JudgeResult


@dataclass
class ScoreOutcome:
    """The computed result of scoring a final image.

    Attributes:
        detail_score: Number of the target details judged present (0..10).
        similarity: The detail-based score fraction, 0.0..1.0. The displayed
            percentage is ``round(similarity * 100)``: the tens digit is the
            number of details captured (``detail_score * 10``) and the ones digit
            is a small tiebreaker derived from raw visual similarity, so a group
            with 8/10 details lands in the 80–89% band and a closer image ranks
            higher within it.
        judge_result: Full per-detail breakdown for the reveal/modal.
    """

    detail_score: int
    similarity: float
    judge_result: JudgeResult


def detail_based_score(detail_score: int, visual_similarity: float) -> float:
    """Combine the detail count and raw visual similarity into one 0..1 fraction.

    The displayed percentage (``round(result * 100)``) always falls in the band
    ``[detail_score*10, detail_score*10 + 9]`` and is capped at 100, so the number
    a group sees reflects how many target details they captured, with a
    visual-similarity nudge that only ever breaks ties within the same band.

    Args:
        detail_score: Number of target details judged present (0..10).
        visual_similarity: Raw image-to-reference similarity, 0.0..1.0.

    Returns:
        The combined score fraction in ``[0.0, 1.0]``.
    """
    # Ones digit (0..9) from visual similarity — a within-band tiebreaker only.
    bonus = min(9, max(0, round(float(visual_similarity) * 9)))
    pct = min(100, detail_score * 10 + bonus)
    return pct / 100.0


def _blank_outcome(details: List[str]) -> ScoreOutcome:
    """Build a zero outcome for a game whose final image is blank.

    Happens only when every step was forfeited (no image ever generated). Every
    detail is marked absent so the reveal page still renders a full breakdown.

    Args:
        details: The 10 target detail phrases.

    Returns:
        A :class:`ScoreOutcome` with score 0 and similarity 0.
    """
    verdicts = [
        DetailVerdict(detail=d, present=False, reason="No image was ever generated.")
        for d in details
    ]
    return ScoreOutcome(detail_score=0, similarity=0.0, judge_result=JudgeResult(verdicts, 0))


def compute_scores(
    final_image_bytes: bytes,
    reference_image_bytes: bytes,
    details: List[str],
    judge: Optional[DetailJudge] = None,
) -> ScoreOutcome:
    """Score a final image against the details and rate similarity.

    Args:
        final_image_bytes: The group's final image bytes.
        reference_image_bytes: The reference image bytes (for the tiebreaker).
        details: The target detail phrases.
        judge: Judge to use; defaults to the configured fallback chain. Injected
            in tests so the mock judge can run without Supabase.

    Returns:
        A :class:`ScoreOutcome` whose ``similarity`` is the detail-based score
        fraction (see :func:`detail_based_score`), not raw visual similarity.
    """
    active_judge = judge or build_judge()
    result = active_judge.score(final_image_bytes, details)
    # Raw visual similarity is used only as a within-band tiebreaker below.
    visual_similarity = active_judge.similarity(final_image_bytes, reference_image_bytes)
    similarity = detail_based_score(result.total, visual_similarity)
    return ScoreOutcome(detail_score=result.total, similarity=similarity, judge_result=result)


def finalize_game(game: dict, final_image_url: Optional[str]) -> dict:
    """Score a finished game, persist the result, and publish to the leaderboard.

    Args:
        game: The game row dict (already advanced through step 3).
        final_image_url: Public URL of the final image, or ``None`` if the whole
            relay produced no image (every step forfeited).

    Returns:
        The updated game row dict, now carrying detail_score/similarity/judge_result
        and marked finished.
    """
    reference = storage.get_reference(game["reference_id"])

    if final_image_url is None:
        # Nothing was ever drawn — score zero without calling the judge.
        outcome = _blank_outcome(reference.details)
    else:
        final_bytes = storage.fetch_image_bytes(final_image_url)
        reference_bytes = storage.reference_image_bytes(reference)
        outcome = compute_scores(final_bytes, reference_bytes, reference.details)

    # Persist the scores onto the game row for the reveal page.
    updated = storage.update_game(
        game["id"],
        {
            "detail_score": outcome.detail_score,
            "similarity": outcome.similarity,
            "judge_result": outcome.judge_result.to_dict(),
            "finished": True,
        },
    )

    # Append to the leaderboard — this is the row the live page observes. Guarded
    # so a second scoring pass (e.g. a refresh of the reveal page while the judge
    # calls are still running) never appends a duplicate row for the same game.
    if not storage.leaderboard_has_game(game["id"]):
        storage.insert_leaderboard(
            game_id=game["id"],
            group_name=game["group_name"],
            detail_score=outcome.detail_score,
            similarity=outcome.similarity,
            final_image_url=final_image_url,
            group_id=game.get("group_id") or "",
        )
    return updated
