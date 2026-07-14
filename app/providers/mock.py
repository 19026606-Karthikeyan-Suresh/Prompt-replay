"""Keyless mock provider — lets the whole game loop run with no API keys.

Design goal: the mock must exercise every downstream code path (generate, edit,
detail scoring, similarity, reveal, leaderboard) *without* any network calls, and
do so deterministically so tests are stable and demos are repeatable.

Trick used here: images produced by :class:`MockImageProvider` carry the running
list of relay prompts inside a PNG text chunk (``relay_prompts``). Because the
edit step reads that chunk from its input image and appends to it, the final
image "remembers" everything the group typed. :class:`MockJudge` then reads the
chunk back and marks a target detail present when the group's prompts mention it
— so the mock score actually reflects play, instead of being random noise.
"""

from __future__ import annotations

import io
import re
from typing import List

from PIL import Image, ImageDraw, ImageFont, PngImagePlugin

from .base import DetailJudge, DetailVerdict, ImageProvider, JudgeResult

# PNG text-chunk key under which we stash the accumulated relay prompts. Kept as
# a module constant so the provider (writer) and judge (reader) never drift.
_PROMPT_CHUNK_KEY = "relay_prompts"

# Canvas size for placeholder images. 512x512 keeps uploads small and renders
# fine on the round/reveal pages.
_CANVAS_SIZE = (512, 512)

# A small palette of background colours; the prompt text selects one
# deterministically so different prompts yield visibly different images.
_PALETTE = [
    (233, 92, 92),
    (92, 148, 233),
    (94, 201, 128),
    (233, 197, 92),
    (168, 108, 224),
    (72, 194, 197),
]

# Words too generic to count as evidence of a detail during mock scoring.
_STOPWORDS = {
    "a", "an", "the", "of", "with", "and", "in", "on", "at", "to", "is",
    "are", "there", "some", "this", "that", "it", "its",
}


def _read_prompt_chunk(image_bytes: bytes) -> str:
    """Read the accumulated relay-prompt text stored in a mock image.

    Args:
        image_bytes: PNG bytes that may carry a ``relay_prompts`` text chunk.

    Returns:
        The stored prompt text, or an empty string if the image has none
        (e.g. it came from a real provider or is a fresh reference image).
    """
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            return img.info.get(_PROMPT_CHUNK_KEY, "") or ""
    except Exception:
        # Any unreadable/foreign image simply contributes no remembered prompts.
        return ""


def _keywords(text: str) -> set[str]:
    """Extract lowercased, meaningful word tokens from a phrase.

    Args:
        text: Arbitrary text (a detail phrase or accumulated prompts).

    Returns:
        The set of lowercased alphabetic tokens with stopwords removed.
    """
    tokens = re.findall(r"[a-zA-Z]+", text.lower())
    return {t for t in tokens if t not in _STOPWORDS and len(t) > 1}


def _render(prompt_history: str) -> bytes:
    """Render a deterministic placeholder image embedding the prompt history.

    Args:
        prompt_history: The full accumulated relay-prompt text to display and
            to persist inside the output PNG's text chunk.

    Returns:
        PNG bytes of a coloured canvas with the (wrapped) prompt text drawn on
        it and the prompt history stored in the ``relay_prompts`` chunk.
    """
    # Pick a background colour deterministically from the text so equal prompts
    # always yield the same colour (stable tests) but different prompts differ.
    colour = _PALETTE[sum(map(ord, prompt_history)) % len(_PALETTE)] if prompt_history else (40, 40, 48)
    img = Image.new("RGB", _CANVAS_SIZE, colour)
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default()  # bundled bitmap font; no TTF file needed

    # Word-wrap the prompt history to ~40 chars/line so it stays on-canvas.
    words = prompt_history.split()
    lines: List[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > 40:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    if not lines:
        lines = ["(blank prompt)"]

    # Draw a small header plus the wrapped prompt text.
    draw.text((16, 16), "MOCK IMAGE", fill=(255, 255, 255), font=font)
    y = 48
    for line in lines[:24]:  # cap lines so very long histories don't overflow
        draw.text((16, y), line, fill=(255, 255, 255), font=font)
        y += 16

    # Persist the prompt history in a PNG text chunk for the judge to read back.
    meta = PngImagePlugin.PngInfo()
    meta.add_text(_PROMPT_CHUNK_KEY, prompt_history)

    buffer = io.BytesIO()
    img.save(buffer, format="PNG", pnginfo=meta)
    return buffer.getvalue()


class MockImageProvider(ImageProvider):
    """Deterministic, keyless image backend for local dev and tests."""

    name = "mock-image"

    def generate(self, prompt: str) -> bytes:
        """Create a placeholder base image from a text prompt.

        Args:
            prompt: The step-1 prompt describing the target image.

        Returns:
            PNG bytes whose ``relay_prompts`` chunk holds ``prompt``.
        """
        return _render(prompt.strip())

    def edit(self, image_bytes: bytes, prompt: str) -> bytes:
        """Edit a placeholder image, accumulating the new prompt.

        The prior prompt history is read from the input image and the new prompt
        appended, so the returned image remembers the full relay so far.

        Args:
            image_bytes: The current image (ideally produced by this provider).
            prompt: The natural-language edit instruction for this step.

        Returns:
            PNG bytes whose ``relay_prompts`` chunk holds the combined history.
        """
        history = _read_prompt_chunk(image_bytes)
        combined = f"{history} {prompt.strip()}".strip() if history else prompt.strip()
        return _render(combined)


class MockJudge(DetailJudge):
    """Deterministic, keyless vision judge for local dev and tests."""

    name = "mock-judge"

    def score(self, image_bytes: bytes, details: List[str]) -> JudgeResult:
        """Score a mock image against the target details by prompt overlap.

        A detail counts as present when at least one of its meaningful keywords
        appears in the relay prompts embedded in the image. This makes the mock
        score track what players actually typed.

        Args:
            image_bytes: The final image to score (a mock PNG with a prompt chunk).
            details: The 10 target detail phrases.

        Returns:
            A :class:`JudgeResult` with a verdict per detail and the total present.
        """
        prompt_words = _keywords(_read_prompt_chunk(image_bytes))
        verdicts: List[DetailVerdict] = []
        for detail in details:
            detail_words = _keywords(detail)
            hits = detail_words & prompt_words
            present = bool(hits)
            if present:
                reason = f"Prompts mentioned: {', '.join(sorted(hits))}."
            else:
                reason = "No matching keywords found in the relay prompts."
            verdicts.append(DetailVerdict(detail=detail, present=present, reason=reason))
        total = sum(1 for v in verdicts if v.present)
        return JudgeResult(verdicts=verdicts, total=total)

    def similarity(self, image_a: bytes, image_b: bytes) -> float:
        """Rate similarity as the keyword overlap between two mock images.

        Uses the Jaccard overlap of the prompt histories embedded in each image.
        Falls back to a stable pseudo-value when either image lacks a chunk, so
        the reveal/leaderboard always have a similarity to display.

        Args:
            image_a: First image (typically the group's final image).
            image_b: Second image (typically the reference image).

        Returns:
            A float in ``[0.0, 1.0]``.
        """
        words_a = _keywords(_read_prompt_chunk(image_a))
        words_b = _keywords(_read_prompt_chunk(image_b))
        if words_a and words_b:
            union = words_a | words_b
            return round(len(words_a & words_b) / len(union), 4)
        # Deterministic fallback derived from byte lengths so it is stable but
        # non-trivial when prompt chunks are unavailable.
        seed = (len(image_a) + len(image_b)) % 100
        return round(0.30 + (seed / 100) * 0.40, 4)  # in [0.30, 0.70]
