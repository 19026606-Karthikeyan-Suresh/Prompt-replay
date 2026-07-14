"""Provider interfaces shared by every AI backend.

Two capabilities are abstracted:

* :class:`ImageProvider` — turn a text prompt into an image (``generate``) or
  edit an existing image with a text prompt (``edit``).
* :class:`DetailJudge` — score a final image against the 10 target details and
  rate visual similarity between two images.

Concrete implementations live in ``gemini.py``, ``openai.py`` and ``mock.py``.
The :class:`FallbackImageProvider` / :class:`FallbackJudge` wrappers (in
``fallback.py``) compose an ordered list of these so a failure in one backend
transparently advances to the next.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List


class ProviderError(Exception):
    """Raised by a single provider when it cannot fulfil a request.

    Fallback wrappers catch this (and any other ``Exception``) to advance to the
    next provider in their ordered list. Carrying a dedicated type simply makes
    intentional provider failures easy to distinguish while debugging.
    """


class AllProvidersFailed(ProviderError):
    """Raised by a fallback wrapper when every provider in its list failed.

    The route layer converts this into a facilitator-visible error so the step
    can be retried, per the spec's "if all providers fail" requirement.
    """


@dataclass
class DetailVerdict:
    """The judge's verdict for a single target detail.

    Attributes:
        detail: The exact detail phrase that was checked (e.g. "a red umbrella").
        present: True if the detail is visibly present in the scored image.
        reason: A short human-readable justification shown on the reveal page.
    """

    detail: str
    present: bool
    reason: str


@dataclass
class JudgeResult:
    """Structured outcome of scoring an image against the 10 details.

    Attributes:
        verdicts: One :class:`DetailVerdict` per target detail, in input order.
        total: Count of details marked present — the score out of 10.
    """

    # Per-detail breakdown, aligned 1:1 with the details list passed to score().
    verdicts: List[DetailVerdict] = field(default_factory=list)
    # Number of details present; kept as an explicit field so callers/templates
    # never have to recompute it (and so a provider can override if it wishes).
    total: int = 0

    def to_dict(self) -> dict:
        """Serialise to a plain dict for JSON storage in the ``games`` row.

        Returns:
            A dict of the form ``{"verdicts": [...], "total": int}`` suitable for
            ``json.dumps`` and for rendering on the reveal page.
        """
        return {
            "verdicts": [
                {"detail": v.detail, "present": v.present, "reason": v.reason}
                for v in self.verdicts
            ],
            "total": self.total,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "JudgeResult":
        """Rebuild a :class:`JudgeResult` from its stored dict form.

        Args:
            data: A dict previously produced by :meth:`to_dict`.

        Returns:
            The reconstructed :class:`JudgeResult`.
        """
        verdicts = [
            DetailVerdict(detail=v["detail"], present=v["present"], reason=v["reason"])
            for v in data.get("verdicts", [])
        ]
        return cls(verdicts=verdicts, total=int(data.get("total", 0)))


def parse_verdicts(data: dict, details: List[str]) -> List[DetailVerdict]:
    """Map a judge's JSON payload back onto the ordered details list.

    Real judges are asked to echo each detail, but we re-align defensively so the
    result always has exactly one verdict per input detail, in the original order,
    regardless of how the model ordered or spelled its echoes.

    Args:
        data: Parsed JSON, expected to contain a ``verdicts`` list of objects
            with ``detail``/``present``/``reason`` keys.
        details: The original ordered detail phrases.

    Returns:
        A list of :class:`DetailVerdict`, one per input detail.
    """
    # Index the model's verdicts by lowercased detail text for order-independent
    # lookup; the model may reorder or re-case the echoed detail strings.
    by_detail = {}
    for item in data.get("verdicts", []) or []:
        key = str(item.get("detail", "")).strip().lower()
        by_detail[key] = item

    verdicts: List[DetailVerdict] = []
    for detail in details:
        item = by_detail.get(detail.strip().lower(), {})
        verdicts.append(
            DetailVerdict(
                detail=detail,
                present=bool(item.get("present", False)),
                reason=str(item.get("reason", "")) or "No reason provided.",
            )
        )
    return verdicts


def parse_similarity(data: dict) -> float:
    """Convert a ``{"similarity": <value>}`` payload into a 0..1 float.

    Accepts either a 0-100 rating or an already-0-1 value and clamps the result.

    Args:
        data: Parsed JSON expected to hold a numeric ``similarity`` field.

    Returns:
        The clamped similarity in ``[0.0, 1.0]``.
    """
    raw = float(data.get("similarity", 0))
    value = raw / 100.0 if raw > 1 else raw
    return max(0.0, min(1.0, value))


class ImageProvider(ABC):
    """Abstract text-to-image and image-to-image backend."""

    #: Human-readable provider name, used in logs and error messages.
    name: str = "image-provider"

    @abstractmethod
    def generate(self, prompt: str) -> bytes:
        """Create a new image from a text prompt (text-to-image).

        Args:
            prompt: The natural-language description of the desired image.

        Returns:
            The generated image encoded as PNG bytes.

        Raises:
            ProviderError: If this backend could not produce an image.
        """

    @abstractmethod
    def edit(self, image_bytes: bytes, prompt: str) -> bytes:
        """Edit an existing image according to a text prompt (image-to-image).

        Args:
            image_bytes: The current image to modify, as PNG/JPEG bytes.
            prompt: The natural-language instruction describing the edit.

        Returns:
            The edited image encoded as PNG bytes.

        Raises:
            ProviderError: If this backend could not produce an image.
        """


class DetailJudge(ABC):
    """Abstract vision judge for detail scoring and similarity rating."""

    #: Human-readable provider name, used in logs and error messages.
    name: str = "detail-judge"

    @abstractmethod
    def score(self, image_bytes: bytes, details: List[str]) -> JudgeResult:
        """Score an image against a list of target details.

        Args:
            image_bytes: The final image to evaluate, as PNG/JPEG bytes.
            details: The exactly-10 target detail phrases to check for.

        Returns:
            A :class:`JudgeResult` with a per-detail present/absent verdict and
            the total number present.

        Raises:
            ProviderError: If this backend could not produce a judgement.
        """

    @abstractmethod
    def similarity(self, image_a: bytes, image_b: bytes) -> float:
        """Rate the visual similarity of two images from 0.0 to 1.0.

        Args:
            image_a: First image (typically the group's final image).
            image_b: Second image (typically the reference image).

        Returns:
            A float in ``[0.0, 1.0]`` where 1.0 means visually identical.

        Raises:
            ProviderError: If this backend could not produce a rating.
        """
