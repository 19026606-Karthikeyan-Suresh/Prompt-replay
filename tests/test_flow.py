"""End-to-end game-flow test with an in-memory fake storage (no Supabase).

Drives a full 3-step relay through :mod:`app.game`, replacing the Supabase
storage layer with an in-memory fake so the generate -> edit -> edit chain,
scoring, and leaderboard write are all exercised offline on the mock provider.
"""

from __future__ import annotations

import uuid

import pytest

from app import game, storage
from app.storage import load_references


class FakeStore:
    """Minimal in-memory stand-in for the Supabase storage layer."""

    def __init__(self):
        """Initialise empty game/image/leaderboard stores and a reference."""
        self.games = {}          # game_id -> row dict
        self.images = {}         # url -> bytes
        self.leaderboard = []    # appended result rows
        # Reuse a real seeded reference so details + local image bytes exist.
        self.reference = load_references()["beach-day"]
        self.reference.public_url = "mem://references/beach-day.png"

    def assign_reference(self):
        """Return the fixed test reference (patched storage.assign_reference)."""
        return self.reference

    def get_reference(self, reference_id):
        """Return the test reference regardless of id."""
        return self.reference

    def ensure_reference_uploaded(self, ref):
        """Pretend the reference is uploaded and return its URL."""
        return ref.public_url

    def create_game(self, group_name, group_size, reference_id):
        """Create and store a new game row, returning it."""
        row = {
            "id": str(uuid.uuid4()),
            "group_name": group_name,
            "group_size": group_size,
            "reference_id": reference_id,
            "current_step": 0,
            "finished": False,
            "prompt_1": None, "prompt_2": None, "prompt_3": None,
            "image_url_1": None, "image_url_2": None, "image_url_3": None,
        }
        self.games[row["id"]] = row
        return row

    def get_game(self, game_id):
        """Return a stored game row (a copy so callers can't mutate in place)."""
        row = self.games.get(game_id)
        return dict(row) if row else None

    def update_game(self, game_id, fields):
        """Merge fields into a stored game row and return the updated copy."""
        self.games[game_id].update(fields)
        return dict(self.games[game_id])

    def upload_generated_image(self, game_id, step, image_bytes):
        """Store image bytes under a synthetic URL and return it."""
        url = f"mem://games/{game_id}/step{step}.png"
        self.images[url] = image_bytes
        return url

    def fetch_image_bytes(self, url):
        """Return previously stored bytes for a synthetic URL."""
        return self.images[url]

    def insert_leaderboard(self, game_id, group_name, detail_score, similarity, final_image_url):
        """Append a leaderboard result row and return it."""
        row = {
            "id": str(uuid.uuid4()),
            "game_id": game_id,
            "group_name": group_name,
            "detail_score": detail_score,
            "similarity": similarity,
            "final_image_url": final_image_url,
        }
        self.leaderboard.append(row)
        return row


@pytest.fixture
def fake_store(monkeypatch):
    """Patch the storage layer with a FakeStore for the duration of a test.

    Args:
        monkeypatch: pytest's attribute patcher.

    Returns:
        The FakeStore instance so assertions can inspect stored state.
    """
    store = FakeStore()
    for attr in (
        "assign_reference", "get_reference", "ensure_reference_uploaded",
        "create_game", "get_game", "update_game", "upload_generated_image",
        "fetch_image_bytes", "insert_leaderboard",
    ):
        monkeypatch.setattr(storage, attr, getattr(store, attr))
    return store


def test_full_relay_three_person(fake_store):
    """A full 3-step relay redraws each turn, scores, and posts a result."""
    new_game = game.create_new_game("Test Crew", 3)
    gid = new_game["id"]

    # Step 1 draws the base image from Player 1's description of the target.
    g1 = game.submit_prompt(gid, 1, "a yellow beach umbrella and a red bucket")
    assert g1["current_step"] == 1
    assert g1["image_url_1"] is not None

    # Step 2 redraws a fresh image from the next player's description.
    g2 = game.submit_prompt(gid, 2, "a blue starfish and a green surfboard")
    assert g2["image_url_2"] is not None
    assert g2["image_url_2"] != g2["image_url_1"]

    # Step 3 redraws again and finalizes.
    g3 = game.submit_prompt(gid, 3, "a striped beach ball and a bright orange sun")
    assert g3["finished"] is True
    assert 0 <= g3["detail_score"] <= 10
    assert g3["judge_result"]["total"] == g3["detail_score"]

    # The result was published to the leaderboard exactly once.
    assert len(fake_store.leaderboard) == 1
    assert fake_store.leaderboard[0]["group_name"] == "Test Crew"

    # Broken telephone: the final image is redrawn from ONLY the last player's
    # prompt, so mock scoring credits step-3 details and drops earlier ones.
    present = {v["detail"]: v["present"] for v in g3["judge_result"]["verdicts"]}
    assert present["a striped beach ball"] is True   # named in step 3
    assert present["a bright orange sun"] is True     # named in step 3
    assert present["a green surfboard"] is False      # named only in step 2 — lost


def test_empty_step1_defers_base_generation(fake_store):
    """A forfeited step 1 keeps the canvas blank; step 2 then generates the base."""
    new_game = game.create_new_game("Slow Starters", 3)
    gid = new_game["id"]

    # Step 1 forfeited (empty) -> still blank.
    g1 = game.submit_prompt(gid, 1, "   ")
    assert g1["image_url_1"] is None
    assert g1["current_step"] == 1

    # Step 2 is the first real prompt -> generates the base image.
    g2 = game.submit_prompt(gid, 2, "a green surfboard and a red bucket")
    assert g2["image_url_2"] is not None

    # Step 3 edits and finishes.
    g3 = game.submit_prompt(gid, 3, "add a white seagull")
    assert g3["finished"] is True
    assert len(fake_store.leaderboard) == 1


def test_duplicate_submit_is_idempotent(fake_store):
    """Re-submitting an already-recorded step does not double-generate."""
    new_game = game.create_new_game("Doublers", 3)
    gid = new_game["id"]
    game.submit_prompt(gid, 1, "a green apple")
    first_url = fake_store.games[gid]["image_url_1"]
    # A stale duplicate submit for step 1 should be a no-op.
    game.submit_prompt(gid, 1, "something else entirely")
    assert fake_store.games[gid]["image_url_1"] == first_url
