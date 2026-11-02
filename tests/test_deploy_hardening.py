"""Deployment hardening: basic auth middleware and container-configurable paths."""

from __future__ import annotations

import base64
import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jobscout import user_config
from jobscout.ui import server


@pytest.fixture()
def client() -> TestClient:
    return TestClient(server.app)


def _basic(user: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_no_password_means_open(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JOBSCOUT_PASSWORD", raising=False)
    assert client.get("/api/settings").status_code == 200


def test_password_gates_every_route(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBSCOUT_PASSWORD", "s3cret")
    monkeypatch.delenv("JOBSCOUT_USERNAME", raising=False)

    anonymous = client.get("/api/settings")
    assert anonymous.status_code == 401
    assert anonymous.headers["WWW-Authenticate"].startswith("Basic")

    wrong = client.get("/api/settings", headers=_basic("jobscout", "nope"))
    assert wrong.status_code == 401
    wrong_user = client.get("/api/settings", headers=_basic("admin", "s3cret"))
    assert wrong_user.status_code == 401

    ok = client.get("/api/settings", headers=_basic("jobscout", "s3cret"))
    assert ok.status_code == 200
    assert "llm" in ok.json()

    # mutating endpoints are gated too
    blocked = client.post("/api/settings/llm", json={"model": "x"})
    assert blocked.status_code == 401


def test_health_stays_open_for_platform_checks(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JOBSCOUT_PASSWORD", "s3cret")
    assert client.get("/api/health").status_code == 200


def test_custom_username_and_garbage_headers(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JOBSCOUT_PASSWORD", "pw")
    monkeypatch.setenv("JOBSCOUT_USERNAME", "ana")
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/settings", headers=_basic("ana", "pw")).status_code == 200

    junk = client.get("/api/settings", headers={"Authorization": "Basic !!!not-base64!!!"})
    assert junk.status_code == 401
    other_scheme = client.get("/api/settings", headers={"Authorization": "Bearer abc"})
    assert other_scheme.status_code == 401


def test_paths_follow_env_for_container_volumes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JOBSCOUT_USER_SETTINGS", str(tmp_path / "settings.local.json"))
    monkeypatch.setenv("JOBSCOUT_GOOGLE_SA_FILE", str(tmp_path / "google-service-account.json"))
    reloaded = importlib.reload(user_config)
    try:
        assert reloaded.USER_SETTINGS_PATH == tmp_path / "settings.local.json"
        assert reloaded.SERVICE_ACCOUNT_PATH == tmp_path / "google-service-account.json"
        # writes land there
        reloaded.update_overlay({"sink": "google"})
        assert (tmp_path / "settings.local.json").exists()
    finally:
        monkeypatch.delenv("JOBSCOUT_USER_SETTINGS", raising=False)
        monkeypatch.delenv("JOBSCOUT_GOOGLE_SA_FILE", raising=False)
        importlib.reload(user_config)  # restore module-level defaults for other tests
