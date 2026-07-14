"""Shared pytest fixtures.

Every test runs against the keyless mock provider and with no Supabase, so the
suite needs no accounts or network. This fixture pins that environment and clears
the cached settings/provider singletons so the config is rebuilt per session.
"""

from __future__ import annotations

import os
import sys

import pytest

# Make the project importable when pytest is run from the repo root.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


@pytest.fixture(autouse=True)
def keyless_mock_env(monkeypatch):
    """Force the mock-only provider chain and clear config caches.

    Args:
        monkeypatch: pytest's environment patcher.

    Yields:
        None. Runs before each test to guarantee a deterministic, keyless setup.
    """
    # No real keys -> the fallback chains contain only the mock provider/judge.
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("ENABLE_MOCK", "true")

    # Settings + provider chains are lru_cached; clear them so the patched env
    # takes effect regardless of import order.
    from app import config

    config.get_settings.cache_clear()
    config.build_image_provider.cache_clear()
    config.build_judge.cache_clear()
    yield
