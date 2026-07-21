"""Supabase persistence layer: game rows, leaderboard, Storage, reference pool.

The FastAPI server is the sole writer and authenticates with the service-role
key, so all reads/writes here bypass RLS. The browser never uses this module —
it only reads the leaderboard via the anon key + realtime.

Everything Supabase-specific is contained here so the rest of the app deals in
plain dicts and :class:`Reference` objects.
"""

from __future__ import annotations

import json
import os
import random
import urllib.request
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Dict, List, Optional

from .config import get_settings

# Absolute path to the committed reference pool folder (references/<id>/...).
_REFERENCES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "references")

# The most groups a single target ("original picture") may be handed to. Groups
# play in turn, so a repeated target is what an earlier group could leak to a
# later one — capping assignment keeps each target fresh for at most two groups.
MAX_GROUPS_PER_REFERENCE = 2


@dataclass
class Reference:
    """A single pre-made reference: its image and exactly 10 target details.

    Attributes:
        id: Stable reference id (matches its folder name under references/).
        details: The exactly-10 detail phrases that define the target image.
        local_image_path: Filesystem path to the committed source PNG.
        public_url: Supabase Storage public URL, filled once the image is
            uploaded (lazily, on first assignment).
    """

    id: str
    details: List[str]
    local_image_path: str
    public_url: Optional[str] = field(default=None)


class StorageNotConfigured(RuntimeError):
    """Raised when Supabase settings are missing but a Supabase call is made.

    Surfaced as a clear message so the facilitator knows to fill in ``.env``.
    """


@lru_cache(maxsize=1)
def _client():
    """Create and cache the Supabase client using the service-role key.

    Returns:
        A ``supabase.Client`` authenticated as the service role (server-side
        writer). Cached for the process lifetime.

    Raises:
        StorageNotConfigured: If the URL or service key is missing.
    """
    settings = get_settings()
    if not settings.supabase_configured:
        raise StorageNotConfigured(
            "Supabase is not configured. Set SUPABASE_URL and SUPABASE_SERVICE_KEY "
            "in your .env (see .env.example)."
        )
    from supabase import create_client  # lazy import keeps SDK optional for tests

    return create_client(settings.supabase_url, settings.supabase_service_key)


# --------------------------------------------------------------------------- #
# Reference pool
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def load_references() -> Dict[str, Reference]:
    """Load the committed reference pool from the references/ folder.

    Each reference lives in ``references/<id>/`` with a ``details.json`` holding
    ``{"id", "details":[...10...]}`` and an ``image.png`` source image. Result is
    cached so the folder is scanned once.

    Returns:
        A dict mapping reference id -> :class:`Reference`.
    """
    pool: Dict[str, Reference] = {}
    if not os.path.isdir(_REFERENCES_DIR):
        return pool

    for entry in sorted(os.listdir(_REFERENCES_DIR)):
        ref_dir = os.path.join(_REFERENCES_DIR, entry)
        details_path = os.path.join(ref_dir, "details.json")
        image_path = os.path.join(ref_dir, "image.png")
        if not os.path.isfile(details_path):
            continue
        with open(details_path, "r", encoding="utf-8") as fh:
            meta = json.load(fh)
        pool[entry] = Reference(
            id=meta.get("id", entry),
            details=list(meta.get("details", [])),
            local_image_path=image_path,
            # A public_url stored in details.json is only a hint; we re-upload to
            # the current project on demand so seeds work against any Supabase.
            public_url=meta.get("public_url"),
        )
    return pool


def reference_image_bytes(ref: Reference) -> bytes:
    """Read a reference's source image bytes from disk.

    Args:
        ref: The reference whose local image to read.

    Returns:
        The PNG bytes of the committed source image.

    Raises:
        FileNotFoundError: If the local image file is missing.
    """
    with open(ref.local_image_path, "rb") as fh:
        return fh.read()


def ensure_reference_uploaded(ref: Reference) -> str:
    """Ensure a reference image exists in Storage and return its public URL.

    Uploads (upsert) the committed source image to ``references/<id>.png`` in the
    bucket the first time it is needed, then caches the URL on the object so
    repeated games reuse it. Making this idempotent lets the seed pool work
    against a fresh Supabase project with no separate upload step.

    Args:
        ref: The reference to publish.

    Returns:
        The public Storage URL of the reference image.
    """
    if ref.public_url:
        return ref.public_url
    image_bytes = reference_image_bytes(ref)
    ref.public_url = upload_image(f"references/{ref.id}.png", image_bytes)
    return ref.public_url


def reference_usage_counts() -> Dict[str, int]:
    """Count how many games have already been assigned each reference.

    A game row is created (and its reference shown to Player 1) the moment a group
    starts, so tallying ``games`` rows by ``reference_id`` is the right measure of
    "how many groups have seen this target." One lightweight select suffices for an
    event's game volume.

    Returns:
        A dict mapping ``reference_id`` -> number of games that used it.
    """
    result = _client().table("games").select("reference_id").execute()
    counts: Dict[str, int] = {}
    for row in result.data or []:
        rid = row.get("reference_id")
        if rid:
            counts[rid] = counts.get(rid, 0) + 1
    return counts


def assign_reference(max_per_reference: int = MAX_GROUPS_PER_REFERENCE) -> Reference:
    """Pick a reference from the pool and ensure it is uploaded to Storage.

    Assignment prefers targets shown to fewer than ``max_per_reference`` groups so
    no single target repeats too often. When every target has hit the cap (a very
    large turnout), it degrades gracefully to a randomly chosen *least-used* target
    rather than blocking the group from starting.

    Args:
        max_per_reference: Cap on how many groups may be handed the same target.

    Returns:
        The assigned :class:`Reference`, with ``public_url`` populated.

    Raises:
        RuntimeError: If the reference pool is empty.
    """
    pool = load_references()
    if not pool:
        raise RuntimeError(
            "No references found. Run scripts/prepare_reference.py to seed the pool."
        )
    counts = reference_usage_counts()

    under_cap = [ref for ref in pool.values() if counts.get(ref.id, 0) < max_per_reference]
    if under_cap:
        # Random among under-cap targets keeps games varied; a target drops out of
        # this list once it reaches the cap, so it's never handed to a 3rd group.
        ref = random.choice(under_cap)
    else:
        # Every target is at the cap — reuse the least-used one so play continues.
        fewest = min(counts.get(ref.id, 0) for ref in pool.values())
        ref = random.choice([ref for ref in pool.values() if counts.get(ref.id, 0) == fewest])

    ensure_reference_uploaded(ref)
    return ref


def get_reference(reference_id: str) -> Reference:
    """Look up a reference by id.

    Args:
        reference_id: The id assigned to a game.

    Returns:
        The matching :class:`Reference`.

    Raises:
        KeyError: If no reference with that id exists in the pool.
    """
    return load_references()[reference_id]


# --------------------------------------------------------------------------- #
# Storage (images bucket)
# --------------------------------------------------------------------------- #
def upload_image(path: str, image_bytes: bytes) -> str:
    """Upload (upsert) image bytes to the public bucket and return its URL.

    Args:
        path: Object path within the bucket (e.g. ``games/<id>/step1.png``).
        image_bytes: The PNG bytes to store.

    Returns:
        The object's public URL.
    """
    settings = get_settings()
    bucket = _client().storage.from_(settings.storage_bucket)
    # upsert="true" replaces an existing object at the same path instead of
    # erroring, which matters for reference re-uploads and step retries.
    bucket.upload(
        path,
        image_bytes,
        {"content-type": "image/png", "upsert": "true"},
    )
    public_url = bucket.get_public_url(path)
    # supabase-py has returned either a bare string or a dict across versions.
    if isinstance(public_url, dict):
        public_url = public_url.get("publicUrl") or public_url.get("public_url", "")
    return public_url


def upload_generated_image(game_id: str, step: int, image_bytes: bytes) -> str:
    """Upload a generated/edited step image under a game's folder.

    Args:
        game_id: The owning game's id.
        step: The step number (1..3) that produced this image.
        image_bytes: The PNG bytes to store.

    Returns:
        The public URL of the uploaded image.
    """
    return upload_image(f"games/{game_id}/step{step}.png", image_bytes)


def fetch_image_bytes(url: str) -> bytes:
    """Download image bytes from a public Storage URL.

    Used to obtain the final image bytes so the AI judge can score it. Public-bucket
    URLs are directly GET-able.

    Args:
        url: The public image URL.

    Returns:
        The raw image bytes.
    """
    with urllib.request.urlopen(url, timeout=30) as response:  # nosec - our own bucket URL
        return response.read()


# --------------------------------------------------------------------------- #
# Game rows
# --------------------------------------------------------------------------- #
def create_game(
    group_name: str, group_size: int, reference_id: str, group_id: str = ""
) -> dict:
    """Insert a new game row and return it.

    Args:
        group_name: The group's chosen name.
        group_size: Number of players (3 or 4).
        reference_id: The assigned reference id.
        group_id: The event group identifier the participants entered (may be "").

    Returns:
        The inserted game row as a dict (including its generated ``id``).
    """
    payload = {
        "group_name": group_name,
        "group_size": group_size,
        "reference_id": reference_id,
        "group_id": group_id,
        "current_step": 0,
        "finished": False,
    }
    result = _client().table("games").insert(payload).execute()
    return result.data[0]


def get_game(game_id: str) -> Optional[dict]:
    """Fetch a game row by id.

    Args:
        game_id: The game's id.

    Returns:
        The game row dict, or ``None`` if no such game exists.
    """
    result = _client().table("games").select("*").eq("id", game_id).limit(1).execute()
    return result.data[0] if result.data else None


def update_game(game_id: str, fields: dict) -> dict:
    """Apply a partial update to a game row.

    Args:
        game_id: The game's id.
        fields: Column -> value pairs to update (e.g. a step's prompt/image_url).

    Returns:
        The updated game row dict.
    """
    result = _client().table("games").update(fields).eq("id", game_id).execute()
    return result.data[0]


def insert_leaderboard(
    game_id: str,
    group_name: str,
    detail_score: int,
    similarity: float,
    final_image_url: Optional[str],
    group_id: str = "",
) -> dict:
    """Append a finished game's result to the leaderboard table.

    This is the write the live leaderboard page observes via realtime.

    Args:
        game_id: The finished game's id.
        group_name: The group's name.
        detail_score: Number of details present (0..10).
        similarity: The detail-based score fraction (0..1); the displayed
            percentage is ``round(similarity * 100)``.
        final_image_url: Public URL of the group's final image.
        group_id: The event group identifier (may be "").

    Returns:
        The inserted leaderboard row dict.
    """
    payload = {
        "game_id": game_id,
        "group_name": group_name,
        "group_id": group_id,
        "detail_score": detail_score,
        "similarity": similarity,
        "final_image_url": final_image_url,
    }
    result = _client().table("leaderboard").insert(payload).execute()
    return result.data[0]


def leaderboard_has_game(game_id: str) -> bool:
    """Return whether a leaderboard row already exists for a game.

    Used to keep scoring idempotent: if the reveal page is scored twice (e.g. a
    refresh during the ~20s judge calls), the second pass must not append a
    duplicate leaderboard row.

    Args:
        game_id: The game's id.

    Returns:
        True if the leaderboard already has a row for this game.
    """
    result = (
        _client().table("leaderboard").select("id").eq("game_id", game_id).limit(1).execute()
    )
    return bool(result.data)


def list_leaderboard() -> List[dict]:
    """Return all leaderboard rows ranked for display.

    Ordered by the displayed score (``similarity`` desc — which now encodes the
    detail-based percentage, tens = details captured, ones = tiebreaker), then
    oldest-first. Ranking by the single shown number keeps the podium/table order
    consistent with the percentages players see.

    Returns:
        A list of leaderboard row dicts in ranked order.
    """
    result = (
        _client()
        .table("leaderboard")
        .select("*")
        .order("similarity", desc=True)
        .order("created_at", desc=False)
        .execute()
    )
    return result.data or []
