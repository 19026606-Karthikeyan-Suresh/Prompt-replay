"""OpenAI implementations of the image and judge interfaces.

These are the fallback providers per the spec (``gpt-image-1`` for images, a
GPT-4o-class vision model for judging). Like the Gemini module, the ``openai``
SDK is imported lazily inside the constructors so it is optional for keyless
runs, and model ids come from :mod:`app.config` (env-overridable).
"""

from __future__ import annotations

import base64
import io
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

# Square size requested from the image API; matches the mock canvas aspect and
# keeps uploads modest. Overridable here if larger art is desired.
_IMAGE_SIZE = "1024x1024"


def _b64_data_url(image_bytes: bytes) -> str:
    """Encode image bytes as a base64 PNG data URL for the vision API.

    Args:
        image_bytes: Encoded image bytes.

    Returns:
        A ``data:image/png;base64,...`` URL string.
    """
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:image/png;base64,{encoded}"


class OpenAIImageProvider(ImageProvider):
    """OpenAI ``gpt-image-1`` text-to-image and image-to-image provider."""

    name = "openai-image"

    def __init__(self, api_key: str, model: str, quality: str = "auto") -> None:
        """Create the OpenAI client and select the image model + quality.

        Args:
            api_key: The OpenAI API key.
            model: The image model id (e.g. ``gpt-image-1-mini``).
            quality: Quality tier ("low"|"medium"|"high"|"auto"). Lower tiers
                generate fewer image tokens, so they are cheaper and faster.
        """
        from openai import OpenAI  # lazy: only needed when configured

        self._client = OpenAI(api_key=api_key)
        self._model = model
        self._quality = quality

    def generate(self, prompt: str) -> bytes:
        """Generate a base image from a text prompt.

        Args:
            prompt: The text-to-image prompt.

        Returns:
            PNG bytes decoded from the API's base64 response.

        Raises:
            ProviderError: On SDK errors or a response with no image.
        """
        try:
            result = self._client.images.generate(
                model=self._model, prompt=prompt, size=_IMAGE_SIZE, quality=self._quality
            )
            return base64.b64decode(result.data[0].b64_json)
        except Exception as exc:
            raise ProviderError(f"OpenAI generate failed: {exc}") from exc

    def edit(self, image_bytes: bytes, prompt: str) -> bytes:
        """Edit an existing image with a text instruction.

        Args:
            image_bytes: The current image to edit.
            prompt: The edit instruction.

        Returns:
            PNG bytes decoded from the API's base64 response.

        Raises:
            ProviderError: On SDK errors or a response with no image.
        """
        try:
            # A named BytesIO lets the SDK infer the upload's filename/type.
            buffer = io.BytesIO(image_bytes)
            buffer.name = "current.png"
            result = self._client.images.edit(
                model=self._model, image=buffer, prompt=prompt, size=_IMAGE_SIZE,
                quality=self._quality,
            )
            return base64.b64decode(result.data[0].b64_json)
        except Exception as exc:
            raise ProviderError(f"OpenAI edit failed: {exc}") from exc


class OpenAIJudge(DetailJudge):
    """OpenAI vision judge for detail scoring and similarity."""

    name = "openai-judge"

    def __init__(self, api_key: str, model: str) -> None:
        """Create the OpenAI client and select the vision model.

        Args:
            api_key: The OpenAI API key.
            model: The vision model id (e.g. ``gpt-4o-mini``).
        """
        from openai import OpenAI  # lazy import

        self._client = OpenAI(api_key=api_key)
        self._model = model

    def score(self, image_bytes: bytes, details: List[str]) -> JudgeResult:
        """Score an image against target details via a JSON-mode chat call.

        Args:
            image_bytes: The final image to evaluate.
            details: The target detail phrases.

        Returns:
            A :class:`JudgeResult` parsed from the model's JSON output.

        Raises:
            ProviderError: On SDK errors or unparseable output.
        """
        instruction = (
            "You are judging whether specific visual details appear in an image. "
            "For EACH detail listed, decide if it is visibly present. Respond ONLY "
            "with JSON of the form "
            '{"verdicts":[{"detail":"...","present":true,"reason":"..."}]}. '
            "Keep each reason under 15 words.\nDetails:\n"
            + "\n".join(f"- {d}" for d in details)
        )
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": instruction},
                            {"type": "image_url", "image_url": {"url": _b64_data_url(image_bytes)}},
                        ],
                    }
                ],
            )
            data = json.loads(response.choices[0].message.content)
        except Exception as exc:
            raise ProviderError(f"OpenAI score failed: {exc}") from exc

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
            response = self._client.chat.completions.create(
                model=self._model,
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": instruction},
                            {"type": "image_url", "image_url": {"url": _b64_data_url(image_a)}},
                            {"type": "image_url", "image_url": {"url": _b64_data_url(image_b)}},
                        ],
                    }
                ],
            )
            data = json.loads(response.choices[0].message.content)
        except Exception as exc:
            raise ProviderError(f"OpenAI similarity failed: {exc}") from exc
        return parse_similarity(data)
