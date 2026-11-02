"""The UI-managed settings overlay: config file, endpoints, masking, tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jobscout import user_config
from jobscout.user_config import (
    deep_merge,
    read_overlay,
    save_service_account,
    sheet_id_from_url,
    update_overlay,
)

SA_JSON = json.dumps(
    {
        "client_email": "bot@project.iam.gserviceaccount.com",
        "private_key": "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----",
        "type": "service_account",
    }
)


@pytest.fixture()
def overlay_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "settings.local.json"
    monkeypatch.setattr(user_config, "USER_SETTINGS_PATH", path)
    return path


def test_overlay_roundtrip_and_merge(overlay_file: Path) -> None:
    assert read_overlay() == {}
    update_overlay({"cover_letter": {"model": "a"}})
    update_overlay({"cover_letter": {"api_key": "sk-1", "model": "b"}, "sink": "google"})
    data = read_overlay()
    assert data == {"cover_letter": {"model": "b", "api_key": "sk-1"}, "sink": "google"}
    assert overlay_file.stat().st_mode & 0o077 == 0  # owner-only, it holds keys
    merged = deep_merge({"a": {"x": 1, "y": 2}}, {"a": {"y": 3}, "b": 4})
    assert merged == {"a": {"x": 1, "y": 3}, "b": 4}


def test_load_settings_applies_overlay(
    overlay_file: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from jobscout import config as config_module

    update_overlay(
        {
            "cover_letter": {"api_key": "sk-overlay", "model": "test/model"},
            "sink": "google",
            "google_sheet_url": "https://docs.google.com/spreadsheets/d/abc123_-xyz456def789ghi0/edit#gid=0",
        }
    )
    monkeypatch.setattr(config_module, "read_overlay", read_overlay)
    monkeypatch.setattr(config_module, "load_dotenv", lambda *_a, **_k: None)
    # Other tests may have loaded the repo .env into os.environ already;
    # a real env var always beats the overlay, so start from a clean slate.
    for var in (
        "COVER_LETTER_MODEL",
        "COVER_LETTER_BASE_URL",
        "COVER_LETTER_TIMEOUT",
        "COVER_LETTER_COMPILE_PDF",
        "SINK",
        "GOOGLE_SERVICE_ACCOUNT_JSON",
        "GOOGLE_SHEET_ID",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("GOOGLE_SHEET_URL", raising=False)
    monkeypatch.delenv("COVER_LETTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.delenv("NVAPI_KEY", raising=False)

    settings = config_module.load_settings(tmp_path / "missing.yaml")
    assert settings.cover_letter.api_key == "sk-overlay"
    assert settings.cover_letter.model == "test/model"
    assert settings.sink == "google"
    # the id was derived from the pasted link
    assert settings.google_sheet_id == "abc123_-xyz456def789ghi0"
    assert settings.google_sheet_url.endswith("#gid=0")


def test_env_still_beats_overlay(
    overlay_file: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from jobscout import config as config_module

    update_overlay({"cover_letter": {"api_key": "sk-overlay"}})
    monkeypatch.setattr(config_module, "read_overlay", read_overlay)
    monkeypatch.setenv("COVER_LETTER_API_KEY", "sk-env")
    settings = config_module.load_settings(tmp_path / "missing.yaml")
    assert settings.cover_letter.api_key == "sk-env"


def test_sheet_id_from_url() -> None:
    assert (
        sheet_id_from_url("https://docs.google.com/spreadsheets/d/1AbC-9/edit#gid=1")
        == "1AbC-9"
    )
    assert sheet_id_from_url("A" * 24) == "A" * 24  # bare id pasted
    assert sheet_id_from_url("https://example.com/x") is None
    assert sheet_id_from_url(None) is None


def test_save_service_account(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "google-service-account.json"
    monkeypatch.setattr(user_config, "SERVICE_ACCOUNT_PATH", target)
    email = save_service_account(SA_JSON)
    assert email == "bot@project.iam.gserviceaccount.com"
    assert target.exists()
    assert target.stat().st_mode & 0o077 == 0
    assert user_config.service_account_email(target) == email
    with pytest.raises(ValueError):
        save_service_account("not json")
    with pytest.raises(ValueError):
        save_service_account(json.dumps({"client_email": "x"}))  # no private_key


# ── endpoints ──────────────────────────────────────────────────────────


def _make_settings_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from fastapi.testclient import TestClient
    from jobscout.ui import server

    overlay = tmp_path / "settings.local.json"
    monkeypatch.setattr(user_config, "USER_SETTINGS_PATH", overlay)
    monkeypatch.setattr(user_config, "SERVICE_ACCOUNT_PATH", tmp_path / "sa.json")
    monkeypatch.setattr(server.settings.cover_letter, "api_key", "sk-server-key-9876")
    monkeypatch.setattr(server.settings, "google_service_account_json", None)
    monkeypatch.setattr(server.settings, "google_sheet_id", None)
    monkeypatch.setattr(server.settings, "google_sheet_url", None)
    monkeypatch.setattr(server.settings, "sink", "auto")
    return TestClient(server.app), server, overlay


def test_settings_get_masks_keys(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client, _server, _overlay = _make_settings_env(monkeypatch, tmp_path)
    response = client.get("/api/settings")
    assert response.status_code == 200
    body = response.json()
    assert body["llm"]["has_key"] is True
    assert body["llm"]["key_hint"] == "****9876"
    assert "sk-server-key-9876" not in response.text


def test_settings_llm_save_persists_and_applies(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client, server, overlay = _make_settings_env(monkeypatch, tmp_path)
    saved = client.post(
        "/api/settings/llm",
        json={
            "provider": "openai",
            "base_url": "https://api.example.com/v1",
            "model": "some/model",
            "api_key": "sk-fresh-1234",
        },
    )
    assert saved.status_code == 200
    assert saved.json()["llm"]["key_hint"] == "****1234"
    # live singleton updated...
    assert server.settings.cover_letter.base_url == "https://api.example.com/v1"
    assert server.settings.cover_letter.api_key == "sk-fresh-1234"
    # ...and on disk for the next start
    stored = json.loads(overlay.read_text(encoding="utf-8"))
    assert stored["cover_letter"]["api_key"] == "sk-fresh-1234"
    # omitted key keeps the stored value
    again = client.post("/api/settings/llm", json={"model": "next/model"})
    assert again.json()["llm"]["key_hint"] == "****1234"
    assert server.settings.cover_letter.api_key == "sk-fresh-1234"
    # "" clears
    cleared = client.post("/api/settings/llm", json={"api_key": ""})
    assert cleared.json()["llm"]["has_key"] is False
    assert server.settings.cover_letter.api_key is None


def test_settings_sink_url_and_service_account(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client, server, _overlay = _make_settings_env(monkeypatch, tmp_path)

    bad = client.post(
        "/api/settings/sink",
        json={"mode": "google", "sheet_url": "https://example.com/not-a-sheet"},
    )
    assert bad.status_code == 422

    saved = client.post(
        "/api/settings/sink",
        json={
            "mode": "google",
            "sheet_url": "https://docs.google.com/spreadsheets/d/xyz123abc456def789/edit",
            "service_account_json": SA_JSON,
        },
    )
    assert saved.status_code == 200
    sink = saved.json()["sink"]
    assert sink["sheet_id"] == "xyz123abc456def789"
    assert sink["has_service_account"] is True
    assert sink["service_account_email"] == "bot@project.iam.gserviceaccount.com"
    assert server.settings.sink == "google"
    assert server.settings.google_sheet_id == "xyz123abc456def789"
    assert Path(str(server.settings.google_service_account_json)).exists()

    sa_path = tmp_path / "sa.json"
    sa_path.unlink()
    after = client.get("/api/settings").json()["sink"]
    assert after["has_service_account"] is False  # state reflects the file


def test_settings_test_endpoints_report_without_raising(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client, server, _overlay = _make_settings_env(monkeypatch, tmp_path)

    class FakeTransport:
        def post(self, payload):
            assert payload["messages"][0]["content"]
            return {"choices": [{"message": {"content": "ready"}}]}

    monkeypatch.setattr("jobscout.llm.build_transport", lambda **_kw: FakeTransport())
    ok = client.post("/api/settings/test/llm")
    assert ok.json() == {"ok": True, "model": server.settings.cover_letter.model,
                         "reply": "ready"}

    def boom(**_kw):
        raise RuntimeError("no key configured")

    monkeypatch.setattr("jobscout.llm.build_transport", boom)
    failed = client.post("/api/settings/test/llm")
    assert failed.json()["ok"] is False and "no key" in failed.json()["error"]

    class FakeSink:
        kind = "google"

        def list_jobs(self, limit=100):
            return []

    monkeypatch.setattr(server, "build_sink", lambda *_a, **_k: FakeSink())
    sink_ok = client.post("/api/settings/test/sink")
    assert sink_ok.json()["ok"] is True and sink_ok.json()["kind"] == "google"

    def sink_boom(*_a, **_k):
        raise RuntimeError("sheet gone")

    monkeypatch.setattr(server, "build_sink", sink_boom)
    sink_bad = client.post("/api/settings/test/sink")
    assert sink_bad.json()["ok"] is False and "sheet gone" in sink_bad.json()["error"]
