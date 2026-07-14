"""Unit tests for the pure game rules, providers, and scoring core."""

from __future__ import annotations

import pytest

from app import game
from app.providers.base import AllProvidersFailed, ImageProvider
from app.providers.fallback import FallbackImageProvider
from app.providers.mock import MockImageProvider, MockJudge
from app.scoring import compute_scores


# --------------------------------------------------------------------------- #
# decide_action / latest_image_url
# --------------------------------------------------------------------------- #
def test_generate_when_blank():
    """A non-empty first prompt with no base image should generate."""
    blank = {"image_url_1": None, "image_url_2": None, "image_url_3": None}
    assert game.decide_action(blank, "a red umbrella") == game.ACTION_GENERATE


def test_edit_when_base_exists():
    """A non-empty prompt with an existing image should edit it."""
    after1 = {"image_url_1": "u1", "image_url_2": None, "image_url_3": None}
    assert game.decide_action(after1, "add a cat") == game.ACTION_EDIT


def test_empty_prompt_carries():
    """An empty/whitespace prompt forfeits the turn (carry)."""
    after1 = {"image_url_1": "u1", "image_url_2": None, "image_url_3": None}
    assert game.decide_action(after1, "   ") == game.ACTION_CARRY


def test_empty_step1_then_generate():
    """If step 1 was blank, the next non-empty prompt still generates the base."""
    blank = {"image_url_1": None, "image_url_2": None, "image_url_3": None}
    # Step 1 forfeited leaves everything null; step 2's real prompt must generate.
    assert game.decide_action(blank, "first real prompt") == game.ACTION_GENERATE


def test_latest_image_url_picks_most_recent():
    """latest_image_url returns the highest completed step's image."""
    assert game.latest_image_url({"image_url_1": "u1", "image_url_2": "u2", "image_url_3": None}) == "u2"
    assert game.latest_image_url({"image_url_1": None, "image_url_2": None, "image_url_3": None}) is None


# --------------------------------------------------------------------------- #
# player_label / next_step
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "size,step,expected",
    [
        (3, 1, "Player 1"),
        (3, 2, "Player 2"),
        (3, 3, "Player 3"),
        (4, 1, "Player 1"),
        (4, 3, "Players 3 & 4 (pair)"),
    ],
)
def test_player_label(size, step, expected):
    """Step 3 pairs players 3 & 4 only for a 4-person group."""
    assert game.player_label(size, step) == expected


def test_next_step_progression():
    """next_step advances until all three steps are done."""
    assert game.next_step({"current_step": 0}) == 1
    assert game.next_step({"current_step": 2}) == 3
    assert game.next_step({"current_step": 3}) == 0


# --------------------------------------------------------------------------- #
# Providers + fallback
# --------------------------------------------------------------------------- #
class _AlwaysFails(ImageProvider):
    """A provider that always raises, to exercise fallback advancement."""

    name = "always-fails"

    def generate(self, prompt):
        raise RuntimeError("boom")

    def edit(self, image_bytes, prompt):
        raise RuntimeError("boom")


def test_fallback_advances_past_failure():
    """A failing primary should fall through to the working mock."""
    chain = FallbackImageProvider([_AlwaysFails(), MockImageProvider()])
    out = chain.generate("a red umbrella")
    assert isinstance(out, bytes) and len(out) > 0


def test_fallback_raises_when_all_fail():
    """When every provider fails, AllProvidersFailed is raised."""
    chain = FallbackImageProvider([_AlwaysFails(), _AlwaysFails()])
    with pytest.raises(AllProvidersFailed):
        chain.generate("anything")


def test_mock_edit_accumulates_prompts():
    """The mock's edit output should remember prior prompts for judging."""
    prov = MockImageProvider()
    b1 = prov.generate("a red umbrella")
    b2 = prov.edit(b1, "a black cat")
    judge = MockJudge()
    result = judge.score(b2, ["a red umbrella", "a black cat", "a purple dragon"])
    present = {v.detail: v.present for v in result.verdicts}
    assert present["a red umbrella"] is True
    assert present["a black cat"] is True
    assert present["a purple dragon"] is False


# --------------------------------------------------------------------------- #
# Scoring core
# --------------------------------------------------------------------------- #
def test_compute_scores_ranges():
    """compute_scores returns a 0..10 score and 0..1 similarity."""
    prov = MockImageProvider()
    final = prov.edit(prov.generate("a red umbrella"), "a black cat and heavy rain")
    reference = prov.generate("a red umbrella a black cat heavy rain a yellow taxi")
    details = [
        "a red umbrella", "a black cat", "heavy rain", "a yellow taxi", "a blue mailbox",
        "a foggy sky", "a streetlamp", "a puddle", "a raincoat", "cobblestones",
    ]
    outcome = compute_scores(final, reference, details, judge=MockJudge())
    assert 0 <= outcome.detail_score <= 10
    assert 0.0 <= outcome.similarity <= 1.0
    assert len(outcome.judge_result.verdicts) == 10
    assert outcome.judge_result.total == outcome.detail_score
