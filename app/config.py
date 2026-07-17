"""Central configuration: environment loading and AI provider assembly.

All secrets and tunables come from environment variables (loaded from a local
``.env`` in development via python-dotenv) — never hard-coded. This module also
builds the ordered provider fallback chains used across the app, so the choice
of which backends run, and in what order, lives in exactly one place.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from typing import List

from dotenv import load_dotenv

from .providers.base import DetailJudge, ImageProvider
from .providers.fallback import FallbackImageProvider, FallbackJudge
from .providers.mock import MockImageProvider, MockJudge

# Load .env once at import so every ``os.getenv`` below sees local overrides.
load_dotenv()


def _get_bool(name: str, default: bool) -> bool:
    """Read a boolean environment variable with a default.

    Args:
        name: Environment variable name.
        default: Value to use when the variable is unset or empty.

    Returns:
        True for "1"/"true"/"yes"/"on" (case-insensitive), else False; the
        default when unset.
    """
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _get_int(name: str, default: int) -> int:
    """Read an integer environment variable with a default.

    Args:
        name: Environment variable name.
        default: Value to use when unset or unparseable.

    Returns:
        The parsed integer, or the default on missing/invalid input.
    """
    raw = os.getenv(name)
    try:
        return int(raw) if raw not in (None, "") else default
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    """Immutable snapshot of all runtime configuration.

    Attributes:
        supabase_url: Base URL of the Supabase project.
        supabase_service_key: Service-role key (server-side writer only).
        supabase_anon_key: Anon key (safe to expose to the browser; read-only).
        storage_bucket: Name of the public Storage bucket for images.
        gemini_api_key: Google Generative AI key, or "" if not configured.
        openai_api_key: OpenAI key, or "" if not configured.
        gemini_image_model: Gemini model id used for image generate/edit.
        gemini_vision_model: Gemini model id used for judging/similarity.
        openai_image_model: OpenAI model id used for image generate/edit.
        openai_vision_model: OpenAI model id used for judging/similarity.
        openai_image_quality: Image quality tier ("low"|"medium"|"high"|"auto").
            Lower tiers generate far fewer image tokens, so they are both cheaper
            AND faster; "low" is the cheapest/fastest.
        provider_order: Ordered provider names to try (e.g. ["gemini","openai"]).
        enable_mock: Whether to append the keyless mock as a final fallback.
        reference_reveal_seconds: Seconds player 1 sees the reference.
        prompt_seconds: Seconds allowed per prompt before auto-submit.
    """

    supabase_url: str
    supabase_service_key: str
    supabase_anon_key: str
    storage_bucket: str

    gemini_api_key: str
    openai_api_key: str

    gemini_image_model: str
    gemini_vision_model: str
    openai_image_model: str
    openai_vision_model: str

    openai_image_quality: str = "low"

    provider_order: List[str] = field(default_factory=list)
    enable_mock: bool = True
    reference_reveal_seconds: int = 30
    prompt_seconds: int = 30

    @property
    def supabase_configured(self) -> bool:
        """Whether the minimum Supabase settings are present.

        Returns:
            True if both the URL and the service-role key are set, meaning the
            server can read/write Postgres and Storage.
        """
        return bool(self.supabase_url and self.supabase_service_key)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Build and cache the :class:`Settings` snapshot from the environment.

    Model ids default to current-generation names but are overridable via env
    because provider model names change over time.

    Returns:
        The cached :class:`Settings` instance for this process.
    """
    # Provider order is a comma-separated list; blanks are ignored.
    order_raw = os.getenv("PROVIDER_ORDER", "gemini,openai")
    provider_order = [p.strip().lower() for p in order_raw.split(",") if p.strip()]

    return Settings(
        supabase_url=os.getenv("SUPABASE_URL", "").strip(),
        supabase_service_key=os.getenv("SUPABASE_SERVICE_KEY", "").strip(),
        supabase_anon_key=os.getenv("SUPABASE_ANON_KEY", "").strip(),
        storage_bucket=os.getenv("SUPABASE_STORAGE_BUCKET", "images").strip(),
        gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip(),
        openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
        # Image + vision model ids — override via env if a provider renames them.
        gemini_image_model=os.getenv(
            "GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image"
        ).strip(),
        gemini_vision_model=os.getenv("GEMINI_VISION_MODEL", "gemini-2.0-flash").strip(),
        # Default to the cheapest/fastest image model + quality; override via env.
        openai_image_model=os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1-mini").strip(),
        openai_vision_model=os.getenv("OPENAI_VISION_MODEL", "gpt-4o-mini").strip(),
        openai_image_quality=os.getenv("OPENAI_IMAGE_QUALITY", "low").strip().lower(),
        provider_order=provider_order,
        enable_mock=_get_bool("ENABLE_MOCK", True),
        reference_reveal_seconds=_get_int("REFERENCE_REVEAL_SECONDS", 30),
        prompt_seconds=_get_int("PROMPT_SECONDS", 30),
    )


@lru_cache(maxsize=1)
def build_image_provider() -> ImageProvider:
    """Assemble the ordered image-provider fallback chain from settings.

    Real providers are added only when their API key is present; the keyless
    mock is appended last when ``ENABLE_MOCK`` is true so the app still runs with
    no keys. Real provider modules are imported lazily so their heavy SDK deps
    are only required when actually configured.

    Returns:
        A :class:`FallbackImageProvider` wrapping the assembled chain.

    Raises:
        RuntimeError: If no providers are available (no keys and mock disabled).
    """
    settings = get_settings()
    chain: List[ImageProvider] = []

    for name in settings.provider_order:
        if name == "gemini" and settings.gemini_api_key:
            from .providers.gemini import GeminiImageProvider  # lazy import

            chain.append(GeminiImageProvider(settings.gemini_api_key, settings.gemini_image_model))
        elif name == "openai" and settings.openai_api_key:
            from .providers.openai import OpenAIImageProvider  # lazy import

            chain.append(
                OpenAIImageProvider(
                    settings.openai_api_key,
                    settings.openai_image_model,
                    settings.openai_image_quality,
                )
            )

    if settings.enable_mock:
        chain.append(MockImageProvider())

    if not chain:
        raise RuntimeError(
            "No image providers configured: set GEMINI_API_KEY or OPENAI_API_KEY, "
            "or set ENABLE_MOCK=true to use the keyless mock provider."
        )
    return FallbackImageProvider(chain)


@lru_cache(maxsize=1)
def build_judge() -> DetailJudge:
    """Assemble the ordered detail-judge fallback chain from settings.

    Mirrors :func:`build_image_provider` but for the vision judge used for detail
    scoring and similarity rating.

    Returns:
        A :class:`FallbackJudge` wrapping the assembled chain.

    Raises:
        RuntimeError: If no judges are available (no keys and mock disabled).
    """
    settings = get_settings()
    chain: List[DetailJudge] = []

    for name in settings.provider_order:
        if name == "gemini" and settings.gemini_api_key:
            from .providers.gemini import GeminiJudge  # lazy import

            chain.append(GeminiJudge(settings.gemini_api_key, settings.gemini_vision_model))
        elif name == "openai" and settings.openai_api_key:
            from .providers.openai import OpenAIJudge  # lazy import

            chain.append(OpenAIJudge(settings.openai_api_key, settings.openai_vision_model))

    if settings.enable_mock:
        chain.append(MockJudge())

    if not chain:
        raise RuntimeError(
            "No detail judges configured: set GEMINI_API_KEY or OPENAI_API_KEY, "
            "or set ENABLE_MOCK=true to use the keyless mock judge."
        )
    return FallbackJudge(chain)
