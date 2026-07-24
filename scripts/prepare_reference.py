"""Offline reference-image preparation for Prompt Relay.

Given exactly 10 details, this creates a reference image using the SAME image AI
the game uses (so the target is achievable in-game), saves it under
``references/<id>/`` as ``image.png`` + ``details.json``, and — when Supabase is
configured — uploads it to the public Storage bucket and records the URL.

Usage:
    # Seed the committed sample pool (uses the mock provider if no keys set).
    # Targets that already have an image.png + details.json are skipped:
    python scripts/prepare_reference.py --seed

    # Regenerate EVERY target at a higher quality tier than live gameplay uses
    # (--quality overrides OPENAI_IMAGE_QUALITY for this run only):
    python scripts/prepare_reference.py --seed --force --quality high

    # Recover a wiped/incorrectly-uploaded Storage bucket: re-upload the committed
    # images to references/<id>.png. No regeneration, so no AI cost:
    python scripts/prepare_reference.py --upload-only

    # Create one reference from explicit details:
    python scripts/prepare_reference.py --id beach-day \\
        --details "a yellow beach umbrella" "a red bucket" ... (exactly 10)

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
        "id": "beach-day",
        "details": [
            "a yellow beach umbrella", "a red bucket", "a blue starfish",
            "a green surfboard", "a white seagull", "a striped beach ball",
            "a sandcastle with a flag", "a pink flip-flop",
            "a coconut drink with a straw", "a bright orange sun",
        ],
    },
    {
        "id": "cozy-cafe",
        "details": [
            "a steaming coffee cup", "a slice of chocolate cake", "a brown teapot",
            "a small green cactus", "a hanging pendant lamp", "a stack of three books",
            "a black cat on a chair", "a chalkboard menu", "a vase of red tulips",
            "a checkered floor",
        ],
    },
    {
        "id": "winter-cabin",
        "details": [
            "a wooden log cabin", "a red front door", "a snowman with a carrot nose",
            "a green pine tree", "a puff of chimney smoke", "a pair of skis",
            "a glowing lantern", "a red sled", "a robin on a branch",
            "a starry night sky",
        ],
    },
    {
        "id": "farm-morning",
        "details": [
            "a red barn", "a white picket fence", "a brown horse", "a yellow chick",
            "a green tractor", "a scarecrow with a straw hat", "a stack of hay bales",
            "a red rooster", "a wooden windmill", "a bright blue sky",
        ],
    },
    {
        "id": "underwater-reef",
        "details": [
            "an orange clownfish", "a purple octopus", "a green sea turtle",
            "a pink coral reef", "a wooden treasure chest", "a yellow submarine",
            "a school of blue fish", "a red crab", "a tall strand of seaweed",
            "a stream of bubbles",
        ],
    },
    {
        "id": "space-station",
        "details": [
            "a silver rocket ship", "a white astronaut", "a red planet with rings",
            "a yellow crescent moon", "a blue Earth", "a bright yellow star",
            "a gray satellite", "a green alien", "a shining comet", "a small planted flag",
        ],
    },
    {
        "id": "city-park",
        "details": [
            "a red picnic blanket", "a wicker picnic basket", "a green park bench",
            "a tall oak tree", "a blue kite", "a brown dog", "a white duck on a pond",
            "a red bicycle", "a yellow frisbee", "a purple flower bush",
        ],
    },
    {
        "id": "birthday-party",
        "details": [
            "a chocolate birthday cake", "a bunch of colorful balloons",
            "a wrapped gift box with a red bow", "a pointed party hat", "a lit candle",
            "a coiled paper streamer", "a cup of orange juice", "a plate of pink cupcakes",
            "a striped drinking straw", "a slice of watermelon",
        ],
    },
    {
        "id": "jungle-safari",
        "details": [
            "a spotted giraffe", "a gray elephant", "a striped zebra", "a green palm tree",
            "a red parrot", "a brown monkey", "a coiled green snake", "a yellow lion",
            "a blue waterfall", "a wooden safari jeep",
        ],
    },
    {
        "id": "desert-oasis",
        "details": [
            "a tall green cactus", "a brown camel", "a golden sand dune", "a small blue pond",
            "a cluster of date palms", "a low red sun", "a green lizard", "a white desert tent",
            "a pink flamingo", "a scattered pile of rocks",
        ],
    },
    {
        "id": "music-concert",
        "details": [
            "a red electric guitar", "a large bass drum", "a silver microphone on a stand",
            "a bright spotlight", "a tall speaker", "a black grand piano",
            "a pair of golden cymbals", "a colorful crowd", "a blue stage curtain",
            "a single music note",
        ],
    },
    {
        "id": "autumn-forest",
        "details": [
            "a tall orange maple tree", "a pile of red leaves", "a brown squirrel",
            "a red mushroom with white spots", "a wooden footbridge", "a small stream",
            "a brown owl on a branch", "a basket of apples", "a red fox", "a gray stone path",
        ],
    },
    {
        "id": "soccer-stadium",
        "details": [
            "a black-and-white soccer ball", "a white goal net", "a green grass field",
            "a player in a red jersey", "a goalkeeper in gloves", "a corner flag",
            "a crowd of fans", "a pair of cleats", "a whistle on a lanyard",
            "a blue sky with clouds",
        ],
    },
    {
        "id": "carnival-fair",
        "details": [
            "a colorful Ferris wheel", "a red-and-white striped tent", "a swirl of cotton candy",
            "a caramel apple", "a wooden carousel horse", "a bunch of balloons",
            "a popcorn cart", "a spinning teacup ride", "a prize game booth",
            "a hot dog with mustard",
        ],
    },
    {
        "id": "toy-room",
        "details": [
            "a red toy car", "a brown teddy bear", "a stack of wooden blocks",
            "a yellow rubber duck", "a green dinosaur figure", "a spinning top",
            "a small toy train", "a rag doll", "a bouncy red ball",
            "a colorful building-block tower",
        ],
    },
]


def upload_only() -> int:
    """Re-upload every committed reference image to Storage without regenerating.

    Recovers from a wiped/incorrectly-uploaded Storage bucket at no AI cost. The
    app serves reference images from the FLAT path ``references/<id>.png`` (see
    :func:`app.storage.ensure_reference_uploaded`); uploading the local
    ``references/`` tree by hand instead produces ``references/<id>/image.png``,
    which nothing reads, so every recorded URL 404s.

    This cannot self-heal at runtime: ``ensure_reference_uploaded`` returns early
    when ``details.json`` already records a ``public_url``, so the app keeps
    serving the dead link rather than re-uploading. Hence this explicit mode.

    Uploads are upsert, so re-running is safe. ``details.json`` is rewritten only
    when the URL actually changes, keeping a no-op run free of spurious diffs.

    Returns:
        The number of references successfully uploaded.
    """
    if not get_settings().supabase_configured:
        raise SystemExit(
            "Supabase is not configured — set SUPABASE_URL and SUPABASE_SERVICE_KEY "
            "in your .env (see .env.example)."
        )

    references = storage.load_references()
    if not references:
        raise SystemExit(
            "No references found on disk. Run --seed first to generate the pool."
        )

    print(f"[prepare_reference] re-uploading {len(references)} reference(s) to Storage")
    uploaded = 0
    for ref_id, ref in sorted(references.items()):
        try:
            image_bytes = storage.reference_image_bytes(ref)
        except FileNotFoundError:
            print(f"[prepare_reference] {ref_id}: SKIPPED — no local image.png")
            continue

        public_url = storage.upload_image(f"references/{ref_id}.png", image_bytes)
        uploaded += 1

        # Keep details.json in step with reality, but only rewrite on a real change.
        details_path = os.path.join(_REFERENCES_DIR, ref_id, "details.json")
        try:
            with open(details_path, "r", encoding="utf-8") as fh:
                meta = json.load(fh)
        except (OSError, ValueError):
            meta = {"id": ref_id, "details": list(ref.details)}
        if meta.get("public_url") != public_url:
            meta["public_url"] = public_url
            with open(details_path, "w", encoding="utf-8") as fh:
                json.dump(meta, fh, indent=2)

        print(f"[prepare_reference] {ref_id}: uploaded ({len(image_bytes) // 1024} KB)")

    print(f"[prepare_reference] done — {uploaded}/{len(references)} uploaded")
    return uploaded


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


def _seed(upload: bool, force: bool = False) -> None:
    """Create the built-in sample references, skipping ones already generated.

    Seeding is idempotent by default: a target that already has both ``image.png``
    and ``details.json`` on disk is left untouched, so re-running ``--seed`` after
    adding new targets only fills in the missing ones (and never overwrites the
    existing, already-good images with fresh random ones). Pass ``force=True`` to
    regenerate every target regardless.

    Args:
        upload: Whether to upload each to Storage (if Supabase is configured).
        force: Regenerate every target even if its files already exist.
    """
    for ref in SEED_REFERENCES:
        ref_dir = os.path.join(_REFERENCES_DIR, ref["id"])
        already_seeded = os.path.isfile(os.path.join(ref_dir, "image.png")) and os.path.isfile(
            os.path.join(ref_dir, "details.json")
        )
        if already_seeded and not force:
            print(f"[prepare_reference] {ref['id']}: exists, skipping (use --force to regenerate)")
            continue
        create_reference(ref["id"], ref["details"], upload=upload)


def main() -> None:
    """Parse CLI arguments and create the requested reference(s)."""
    parser = argparse.ArgumentParser(description="Prepare Prompt Relay reference images.")
    parser.add_argument("--seed", action="store_true", help="Create the built-in sample pool.")
    parser.add_argument(
        "--upload-only", action="store_true",
        help="Re-upload the committed reference images to Storage without "
             "regenerating them (no AI cost). Use to recover a wiped bucket.",
    )
    parser.add_argument("--id", help="Reference id / folder name.")
    parser.add_argument("--details", nargs="+", help="Exactly 10 detail phrases.")
    parser.add_argument("--from-json", help="Path to a JSON file with {id, details}.")
    parser.add_argument("--no-upload", action="store_true", help="Do not upload to Storage.")
    parser.add_argument(
        "--force", action="store_true",
        help="With --seed, regenerate targets even if they already exist on disk.",
    )
    parser.add_argument(
        "--quality", choices=["low", "medium", "high", "auto"],
        help="Image quality tier for THIS run only (default: OPENAI_IMAGE_QUALITY). "
             "Reference art is usually generated higher than the in-game setting.",
    )
    args = parser.parse_args()

    # OPENAI_IMAGE_QUALITY is a global knob shared with live gameplay, where it is
    # deliberately kept low (cheaper + fast enough to dodge serverless timeouts).
    # Reference art is generated once, offline, so it can afford a higher tier —
    # override the env for this process only, then rebuild the cached settings and
    # provider chain so the new tier actually takes effect.
    if args.quality:
        os.environ["OPENAI_IMAGE_QUALITY"] = args.quality
        get_settings.cache_clear()
        build_image_provider.cache_clear()
        print(f"[prepare_reference] image quality for this run: {args.quality}")

    upload = not args.no_upload

    if args.upload_only:
        # Checked before --seed so the no-cost recovery path can never be
        # confused with a regeneration run.
        upload_only()
    elif args.seed:
        _seed(upload, force=args.force)
    elif args.from_json:
        with open(args.from_json, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        create_reference(data["id"], list(data["details"]), upload=upload)
    elif args.id and args.details:
        create_reference(args.id, args.details, upload=upload)
    else:
        parser.error(
            "Provide --seed, --upload-only, --from-json, or both --id and --details."
        )


if __name__ == "__main__":
    main()
