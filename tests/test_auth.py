"""Tests for the static password gate (middleware + /login).

These run fully offline: the gated routes exercised here (redirect, /login,
/healthz, static) never touch Supabase. The autouse ``keyless_mock_env`` fixture
keeps AI keys unset; each test controls ``SITE_PASSWORD`` itself and clears the
cached settings so the gate flips on/off deterministically.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import config, main


@pytest.fixture
def gated_client(monkeypatch):
    """A TestClient with the password gate ENABLED and redirects not followed."""
    monkeypatch.setenv("SITE_PASSWORD", "s3cret")
    monkeypatch.delenv("SITE_SECRET", raising=False)
    config.get_settings.cache_clear()
    return TestClient(main.app, follow_redirects=False)


def test_unauthed_redirects_to_login(gated_client):
    """A protected page without a valid cookie bounces to /login."""
    resp = gated_client.get("/")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_public_paths_bypass_gate(gated_client):
    """Login, health check, and static assets are reachable without auth."""
    assert gated_client.get("/healthz").status_code == 200
    assert gated_client.get("/static/css/styles.css").status_code == 200
    assert gated_client.get("/login").status_code == 200


def test_wrong_password_rejected(gated_client):
    """An incorrect password re-renders the login page with a 401."""
    resp = gated_client.post("/login", data={"password": "nope"})
    assert resp.status_code == 401


def test_correct_password_sets_cookie(gated_client):
    """The right password issues the auth cookie and redirects home."""
    resp = gated_client.post("/login", data={"password": "s3cret"})
    assert resp.status_code == 303
    assert "pr_auth" in resp.headers.get("set-cookie", "")


def test_valid_cookie_allows_access(gated_client):
    """A request carrying a valid signed cookie passes the gate."""
    token = main._make_auth_token(config.get_settings().cookie_secret)
    resp = gated_client.get("/", cookies={"pr_auth": token})
    assert resp.status_code == 200


def test_gate_disabled_when_no_password(monkeypatch):
    """With no SITE_PASSWORD the site is open (dev/tests) — no redirect."""
    monkeypatch.delenv("SITE_PASSWORD", raising=False)
    config.get_settings.cache_clear()
    client = TestClient(main.app, follow_redirects=False)
    assert client.get("/").status_code == 200
