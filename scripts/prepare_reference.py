"""Offline reference-image preparation for Prompt Relay.

Given exactly 10 details, this creates a reference image using the SAME image AI
the game uses (so the target is achievable in-game), saves it under
``references/<id>/`` as ``image.png`` + ``details.json``, and — when Supabase is
configured — uploads it to the public Storage bucket and records the URL.

Usage:
    # Seed the committed sample pool (uses the mock provider if no keys set):
    python scripts/prepare_reference.py --seed

    # Create one reference from explicit details:
    python scripts/prepare_reference.py --id rainy-street \\
        --details "a red umbrella" "a black cat" ... (exactly 10)

    # Create from a JSON file: {"id": "...", "details": ["...", ... 10]}
    python scripts/prepare_reference.py --from-json my_ref.json

    # Skip the Storage upload (e.g. when seeding without Supabase creds):
    python scripts/prepare_reference.py --seed --no-upload
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# Allow running as a plain script: put the project root on sys.path so the
# ``app`` package imports resolve regardless of the current directory.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from app.config import build_image_provider, get_settings  # noqa: E402
from app import storage  # noqa: E402

# Directory holding the committed reference pool.
_REFERENCES_DIR = os.path.join(_PROJECT_ROOT, "references")

# Built-in sample references, each defined by exactly 10 details. Seeding these
# gives the app a playable pool out of the box.
SEED_REFERENCES = [
    {
        "id": "rainy-street",
        "details": [
            "a red umbrella", "a black cat", "heavy rain", "a yellow taxi",
            "a glowing streetlamp", "a puddle reflection", "a blue mailbox",
            "a person in a raincoat", "wet cobblestones", "a foggy grey sky",
        ],
    },
    {
        "id": "sunny-picnic",
        "details": [
            "a checkered picnic blanket", "a wicker basket", "a green apple",
            "two red balloons", "a golden retriever", "a wooden guitar",
            "a glass lemonade jug", "an orange butterfly", "a tall oak tree",
            "a kite in the sky",
        ],
    },
    {
        "id": "space-scene",
        "details": [
            "a silver rocket", "a ringed planet", "three small moons",
            "a floating astronaut", "a bright yellow star", "a red robot",
            "a glass dome habitat", "a trail of stardust", "a blue nebula",
            "a green alien plant",
        ],
    },
]


def build_prompt(details: list[str]) -> str:
    """Compose a single text-to-image prompt from the 10 details.

    Args:
        details: The exactly-10 detail phrases defining the reference.

    Returns:
        A prompt asking for one coherent scene containing every detail.
    """
    joined = "; ".join(details)
    return f"A single coherent illustration of one scene containing: {joined}."


def create_reference(ref_id: str, details: list[str], upload: bool = True) -> str:
    """Generate, save, and (optionally) upload one reference.

    Args:
        ref_id: Stable id / folder name for the reference.
        details: The exactly-10 detail phrases.
        upload: Whether to upload to Supabase Storage (requires configuration).

    Returns:
        The local path to the saved reference image.

    Raises:
        ValueError: If ``details`` does not contain exactly 10 items.
    """
    if len(details) != 10:
        raise ValueError(f"A reference needs exactly 10 details, got {len(details)}.")

    provider = build_image_provider()
    image_bytes = provider.generate(build_prompt(details))

    # Persist the source image + metadata under references/<id>/.
    ref_dir = os.path.join(_REFERENCES_DIR, ref_id)
    os.makedirs(ref_dir, exist_ok=True)
    image_path = os.path.join(ref_dir, "image.png")
    with open(image_path, "wb") as fh:
        fh.write(image_bytes)

    # public_url is only recorded when we actually uploaded; otherwise it stays
    # null and the app uploads on first use (see storage.ensure_reference_uploaded).
    public_url = None
    if upload and get_settings().supabase_configured:
        public_url = storage.upload_image(f"references/{ref_id}.png", image_bytes)

    meta = {"id": ref_id, "details": details, "public_url": public_url}
    with open(os.path.join(ref_dir, "details.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2)

    status = f"uploaded -> {public_url}" if public_url else "saved locally (not uploaded)"
    print(f"[prepare_reference] {ref_id}: {status}")
    return image_path


def _seed(upload: bool) -> None:
    """Create every built-in sample reference.

    Args:
        upload: Whether to upload each to Storage (if Supabase is configured).
    """
    for ref in SEED_REFERENCES:
        create_reference(ref["id"], ref["details"], upload=upload)


def main() -> None:
    """Parse CLI arguments and create the requested reference(s)."""
    parser = argparse.ArgumentParser(description="Prepare Prompt Relay reference images.")
    parser.add_argument("--seed", action="store_true", help="Create the built-in sample pool.")
    parser.add_argument("--id", help="Reference id / folder name.")
    parser.add_argument("--details", nargs="+", help="Exactly 10 detail phrases.")
    parser.add_argument("--from-json", help="Path to a JSON file with {id, details}.")
    parser.add_argument("--no-upload", action="store_true", help="Do not upload to Storage.")
    args = parser.parse_args()

    upload = not args.no_upload

    if args.seed:
        _seed(upload)
    elif args.from_json:
        with open(args.from_json, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        create_reference(data["id"], list(data["details"]), upload=upload)
    elif args.id and args.details:
        create_reference(args.id, args.details, upload=upload)
    else:
        parser.error("Provide --seed, --from-json, or both --id and --details.")


if __name__ == "__main__":
    main()
