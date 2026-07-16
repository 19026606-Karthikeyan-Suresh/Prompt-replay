"""Google Gemini implementations of the image and judge interfaces.

Uses the current **google-genai** SDK (`from google import genai`); the older
`google-generativeai` package is end-of-life and lacks image `response_modalities`
support. These are the primary (free-tier) providers per the spec, imported and
constructed only when ``GEMINI_API_KEY`` is set, so the SDK stays optional for
keyless/mock runs.

Model ids are injected from :mod:`app.config` (env-overridable) because Google
renames preview image/vision models over time — if a call 404s on the model id,
update ``GEMINI_IMAGE_MODEL`` / ``GEMINI_VISION_MODEL`` rather than this file.
"""

from __future__ import annotations

import json
from typing import List

from .base import (
    DetailJudge,
    ImageProvider,
    JudgeResult,
    ProviderError,
    parse_similarity,
    parse_verdicts,
)


def _extract_image(response) -> bytes:
    """Pull the first inline image out of a genai generate_content response.

    Args:
        response: The object returned by ``client.models.generate_content``.

    Returns:
        The raw image bytes of the first image part found.

    Raises:
        ProviderError: If the response contained no image part.
    """
    # The response carries a list of parts; an image part has ``inline_data.data``.
    for candidate in getattr(response, "candidates", []) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", []) or []:
            inline = getattr(part, "inline_data", None)
            if inline is not None and getattr(inline, "data", None):
                return inline.data
    raise ProviderError("Gemini response contained no image data.")


class GeminiImageProvider(ImageProvider):
    """Gemini text-to-image and image-to-image provider."""

    name = "gemini-image"

    def __init__(self, api_key: str, model: str) -> None:
        """Create the genai client and select the image model.

        Args:
            api_key: The Google Generative AI API key.
            model: The image-capable model id (from settings).
        """
        from google import genai  # lazy: only needed when configured
        from google.genai import types

        self._types = types
        self._client = genai.Client(api_key=api_key)
        self._model = model
        # Image-capable models must be asked for an IMAGE modality explicitly.
        self._image_config = types.GenerateContentConfig(
            response_modalities=["TEXT", "IMAGE"]
        )

    def generate(self, prompt: str) -> bytes:
        """Generate a base image from a text prompt.

        Args:
            prompt: The text-to-image prompt.

        Returns:
            Image bytes of the generated image.

        Raises:
            ProviderError: On SDK errors or a response with no image.
        """
        try:
            response = self._client.models.generate_content(
                model=self._model, contents=[prompt], config=self._image_config
            )
        except Exception as exc:  # normalise SDK errors to our type for fallback
            raise ProviderError(f"Gemini generate failed: {exc}") from exc
        return _extract_image(response)

    def edit(self, image_bytes: bytes, prompt: str) -> bytes:
        """Edit an existing image with a text instruction.

        Args:
            image_bytes: The current image to edit.
            prompt: The edit instruction.

        Returns:
            Image bytes of the edited image.

        Raises:
            ProviderError: On SDK errors or a response with no image.
        """
        try:
            # Passing [prompt, image] instructs the model to edit the image.
            image_part = self._types.Part.from_bytes(data=image_bytes, mime_type="image/png")
            response = self._client.models.generate_content(
                model=self._model,
                contents=[prompt, image_part],
                config=self._image_config,
            )
        except Exception as exc:
            raise ProviderError(f"Gemini edit failed: {exc}") from exc
        return _extract_image(response)


class GeminiJudge(DetailJudge):
    """Gemini vision judge for detail scoring and similarity."""

    name = "gemini-judge"

    def __init__(self, api_key: str, model: str) -> None:
        """Create the genai client and select the vision model.

        Args:
            api_key: The Google Generative AI API key.
            model: The vision-capable model id (from settings).
        """
        from google import genai  # lazy import
        from google.genai import types

        self._types = types
        self._client = genai.Client(api_key=api_key)
        self._model = model
        # Force JSON output so we can parse deterministically.
        self._json_config = types.GenerateContentConfig(
            response_mime_type="application/json"
        )

    def score(self, image_bytes: bytes, details: List[str]) -> JudgeResult:
        """Score an image against target details via a structured JSON prompt.

        Args:
            image_bytes: The final image to evaluate.
            details: The target detail phrases.

        Returns:
            A :class:`JudgeResult` parsed from the model's JSON output.

        Raises:
            ProviderError: On SDK errors or unparseable output.
        """
        # The prompt pins the exact JSON shape we parse below.
        instruction = (
            "You are judging whether specific visual details appear in an image. "
            "For EACH detail below, decide if it is visibly present. Respond ONLY "
            "with JSON of the form "
            '{"verdicts":[{"detail":"...","present":true,"reason":"..."}]}. '
            "Keep each reason under 15 words.\nDetails:\n"
            + "\n".join(f"- {d}" for d in details)
        )
        try:
            image_part = self._types.Part.from_bytes(data=image_bytes, mime_type="image/png")
            response = self._client.models.generate_content(
                model=self._model,
                contents=[instruction, image_part],
                config=self._json_config,
            )
            data = json.loads(response.text)
        except Exception as exc:
            raise ProviderError(f"Gemini score failed: {exc}") from exc

        verdicts = parse_verdicts(data, details)
        total = sum(1 for v in verdicts if v.present)
        return JudgeResult(verdicts=verdicts, total=total)

    def similarity(self, image_a: bytes, image_b: bytes) -> float:
        """Rate visual similarity of two images as a 0..1 float.

        Args:
            image_a: First image.
            image_b: Second image.

        Returns:
            Similarity in ``[0.0, 1.0]``.

        Raises:
            ProviderError: On SDK errors or unparseable output.
        """
        instruction = (
            "Rate how visually similar these two images are on a 0-100 scale "
            "(100 = identical). Respond ONLY with JSON: {\"similarity\": <int>}."
        )
        try:
            part_a = self._types.Part.from_bytes(data=image_a, mime_type="image/png")
            part_b = self._types.Part.from_bytes(data=image_b, mime_type="image/png")
            response = self._client.models.generate_content(
                model=self._model,
                contents=[instruction, part_a, part_b],
                config=self._json_config,
            )
            data = json.loads(response.text)
        except Exception as exc:
            raise ProviderError(f"Gemini similarity failed: {exc}") from exc
        return parse_similarity(data)
