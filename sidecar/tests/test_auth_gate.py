"""The HTTP auth gate: fail-closed startup, and the GET-only media token.

The sidecar executable ships inside the user's install directory, so a copy
started outside the Tauri shell must not serve an unauthenticated API. And the
`?_t=` param that <img>/<audio> loads depend on must not be able to write
anything, because a URL is copied into the WebView's disk cache and into any
access log that is enabled.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import main


HEALTH = "/api/v1/health"


@pytest.fixture
def authed(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A client whose sidecar has both secrets configured."""
    monkeypatch.setattr(main, "_TOKEN", "s" * 64)
    monkeypatch.setattr(main, "_MEDIA_TOKEN", "m" * 64)
    monkeypatch.setattr(main, "_ALLOW_NO_AUTH", False)
    return TestClient(main.create_app())


def test_no_token_and_no_optout_refuses_to_serve(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unconfigured sidecar must fail closed, not wave every request through."""
    monkeypatch.setattr(main, "_TOKEN", "")
    monkeypatch.setattr(main, "_ALLOW_NO_AUTH", False)

    res = TestClient(main.create_app()).get(HEALTH)

    assert res.status_code == 503
    assert res.json()["detail"]["error"] == "AUTH_NOT_CONFIGURED"


def test_no_token_with_explicit_optout_serves(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dev and pytest opt out deliberately; that path stays open."""
    monkeypatch.setattr(main, "_TOKEN", "")
    monkeypatch.setattr(main, "_ALLOW_NO_AUTH", True)

    assert TestClient(main.create_app()).get(HEALTH).status_code == 200


def test_session_token_on_header_is_accepted(authed: TestClient) -> None:
    res = authed.get(HEALTH, headers={"X-MercWizard-Token": "s" * 64})
    assert res.status_code == 200


def test_session_token_in_query_is_rejected(authed: TestClient) -> None:
    """The full-access token must never be usable from a URL."""
    res = authed.get(HEALTH, params={"_t": "s" * 64})
    assert res.status_code == 401


def test_media_token_allows_get(authed: TestClient) -> None:
    res = authed.get(HEALTH, params={"_t": "m" * 64})
    assert res.status_code == 200


def test_media_token_cannot_write(authed: TestClient) -> None:
    """A leaked media token must not reach a mutating route."""
    res = authed.post(
        "/api/v1/installs/register",
        params={"_t": "m" * 64},
        json={"path": "C:/nowhere"},
    )
    assert res.status_code == 401


def test_missing_token_is_rejected(authed: TestClient) -> None:
    assert authed.get(HEALTH).status_code == 401


def test_media_token_endpoint_requires_the_session_token(authed: TestClient) -> None:
    """The issuing route is itself gated, and hands back the media secret."""
    assert authed.get("/api/v1/auth/media-token").status_code == 401

    res = authed.get(
        "/api/v1/auth/media-token", headers={"X-MercWizard-Token": "s" * 64}
    )
    assert res.status_code == 200
    assert res.json()["token"] == "m" * 64
