"""Fallback wrappers that chain providers in priority order.

Each wrapper holds an ordered list of concrete providers. For every call it
tries them in turn; the first success wins, and any exception (network error,
rate limit, quota, malformed response) is caught so the next provider is tried.
If every provider fails, :class:`AllProvidersFailed` is raised so the route layer
can surface a clear, retryable error to the facilitator.
"""

from __future__ import annotations

import logging
from typing import List

from .base import (
    AllProvidersFailed,
    DetailJudge,
    ImageProvider,
    JudgeResult,
)

logger = logging.getLogger("prompt_relay.providers")


class FallbackImageProvider(ImageProvider):
    """An :class:`ImageProvider` that delegates to an ordered list of providers."""

    name = "fallback-image"

    def __init__(self, providers: List[ImageProvider]) -> None:
        """Store the ordered provider chain.

        Args:
            providers: Non-empty list of image providers, highest priority first.

        Raises:
            ValueError: If ``providers`` is empty.
        """
        if not providers:
            raise ValueError("FallbackImageProvider requires at least one provider.")
        # The chain is fixed for the process lifetime; order == priority.
        self._providers = providers

    def generate(self, prompt: str) -> bytes:
        """Generate an image, advancing through the chain on failure.

        Args:
            prompt: The text-to-image prompt.

        Returns:
            PNG bytes from the first provider that succeeds.

        Raises:
            AllProvidersFailed: If every provider in the chain fails.
        """
        errors: List[str] = []
        for provider in self._providers:
            try:
                return provider.generate(prompt)
            except Exception as exc:  # broad: any backend failure should fall through
                logger.warning("Image provider %s failed on generate: %s", provider.name, exc)
                errors.append(f"{provider.name}: {exc}")
        raise AllProvidersFailed("All image providers failed on generate — " + " | ".join(errors))

    def edit(self, image_bytes: bytes, prompt: str) -> bytes:
        """Edit an image, advancing through the chain on failure.

        Args:
            image_bytes: The current image to edit.
            prompt: The image-to-image edit instruction.

        Returns:
            PNG bytes from the first provider that succeeds.

        Raises:
            AllProvidersFailed: If every provider in the chain fails.
        """
        errors: List[str] = []
        for provider in self._providers:
            try:
                return provider.edit(image_bytes, prompt)
            except Exception as exc:
                logger.warning("Image provider %s failed on edit: %s", provider.name, exc)
                errors.append(f"{provider.name}: {exc}")
        raise AllProvidersFailed("All image providers failed on edit — " + " | ".join(errors))


class FallbackJudge(DetailJudge):
    """A :class:`DetailJudge` that delegates to an ordered list of judges."""

    name = "fallback-judge"

    def __init__(self, judges: List[DetailJudge]) -> None:
        """Store the ordered judge chain.

        Args:
            judges: Non-empty list of judges, highest priority first.

        Raises:
            ValueError: If ``judges`` is empty.
        """
        if not judges:
            raise ValueError("FallbackJudge requires at least one judge.")
        self._judges = judges

    def score(self, image_bytes: bytes, details: List[str]) -> JudgeResult:
        """Score details, advancing through the chain on failure.

        Args:
            image_bytes: The final image to score.
            details: The target detail phrases.

        Returns:
            The :class:`JudgeResult` from the first judge that succeeds.

        Raises:
            AllProvidersFailed: If every judge in the chain fails.
        """
        errors: List[str] = []
        for judge in self._judges:
            try:
                return judge.score(image_bytes, details)
            except Exception as exc:
                logger.warning("Judge %s failed on score: %s", judge.name, exc)
                errors.append(f"{judge.name}: {exc}")
        raise AllProvidersFailed("All judges failed on score — " + " | ".join(errors))

    def similarity(self, image_a: bytes, image_b: bytes) -> float:
        """Rate similarity, advancing through the chain on failure.

        Args:
            image_a: First image.
            image_b: Second image.

        Returns:
            The similarity float from the first judge that succeeds.

        Raises:
            AllProvidersFailed: If every judge in the chain fails.
        """
        errors: List[str] = []
        for judge in self._judges:
            try:
                return judge.similarity(image_a, image_b)
            except Exception as exc:
                logger.warning("Judge %s failed on similarity: %s", judge.name, exc)
                errors.append(f"{judge.name}: {exc}")
        raise AllProvidersFailed("All judges failed on similarity — " + " | ".join(errors))
