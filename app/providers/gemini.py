"""Google Gemini implementations of the image and judge interfaces.

These are the primary (free-tier) providers per the spec. They are only imported
and constructed when ``GEMINI_API_KEY`` is set, so the ``google-generativeai``
dependency is optional for keyless/mock runs.

Model ids are injected from :mod:`app.config` (env-overridable) because Google
renames preview image/vision models over time — if a call 404s on the model id,
update ``GEMINI_IMAGE_MODEL`` / ``GEMINI_VISION_MODEL`` rather than this file.
"""

from __future__ import annotations

import io
import json
from typing import List

from PIL import Image

from .base import (
    DetailJudge,
    ImageProvider,
    JudgeResult,
    ProviderError,
    parse_similarity,
    parse_verdicts,
)


def _bytes_to_pil(image_bytes: bytes) -> "Image.Image":
    """Decode raw image bytes into a PIL image for the SDK to consume.

    Args:
        image_bytes: Encoded image bytes (PNG/JPEG).

    Returns:
        A decoded RGB :class:`PIL.Image.Image`.
    """
    return Image.open(io.BytesIO(image_bytes)).convert("RGB")


def _extract_image(response) -> bytes:
    """Pull the first inline image out of a Gemini generate_content response.

    Args:
        response: The object returned by ``model.generate_content``.

    Returns:
        The raw image bytes of the first image part found.

    Raises:
        ProviderError: If the response contained no image part.
    """
    # Gemini returns a list of parts; an image part carries ``inline_data.data``.
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
        """Configure the Gemini client and select the image model.

        Args:
            api_key: The Google Generative AI API key.
            model: The image-capable model id (from settings).
        """
        import google.generativeai as genai  # lazy: only needed when configured

        genai.configure(api_key=api_key)
        # Keep references so each call can rebuild request config cheaply.
        self._genai = genai
        self._model_id = model
        self._model = genai.GenerativeModel(model)
        # Image-capable models must be asked for an IMAGE modality explicitly.
        self._image_config = genai.types.GenerationConfig(
            response_modalities=["TEXT", "IMAGE"]
        )

    def generate(self, prompt: str) -> bytes:
        """Generate a base image from a text prompt.

        Args:
            prompt: The text-to-image prompt.

        Returns:
            PNG/JPEG bytes of the generated image.

        Raises:
            ProviderError: On SDK errors or a response with no image.
        """
        try:
            response = self._model.generate_content(
                [prompt], generation_config=self._image_config
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
            PNG/JPEG bytes of the edited image.

        Raises:
            ProviderError: On SDK errors or a response with no image.
        """
        try:
            # Passing [prompt, image] instructs the model to edit the image.
            response = self._model.generate_content(
                [prompt, _bytes_to_pil(image_bytes)],
                generation_config=self._image_config,
            )
        except Exception as exc:
            raise ProviderError(f"Gemini edit failed: {exc}") from exc
        return _extract_image(response)


class GeminiJudge(DetailJudge):
    """Gemini vision judge for detail scoring and similarity."""

    name = "gemini-judge"

    def __init__(self, api_key: str, model: str) -> None:
        """Configure the Gemini client and select the vision model.

        Args:
            api_key: The Google Generative AI API key.
            model: The vision-capable model id (from settings).
        """
        import google.generativeai as genai  # lazy import

        genai.configure(api_key=api_key)
        self._genai = genai
        self._model = genai.GenerativeModel(model)
        # Force JSON output so we can parse deterministically.
        self._json_config = genai.types.GenerationConfig(
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
            response = self._model.generate_content(
                [instruction, _bytes_to_pil(image_bytes)],
                generation_config=self._json_config,
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
            response = self._model.generate_content(
                [instruction, _bytes_to_pil(image_a), _bytes_to_pil(image_b)],
                generation_config=self._json_config,
            )
            data = json.loads(response.text)
        except Exception as exc:
            raise ProviderError(f"Gemini similarity failed: {exc}") from exc
        return parse_similarity(data)
