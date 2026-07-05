from __future__ import annotations

import importlib
from pathlib import Path

from fastapi.testclient import TestClient


def build_client(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PANEL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PANEL_DB_PATH", str(tmp_path / "data" / "panel.db"))
    monkeypatch.setenv("PANEL_RUNTIME_DIR", str(tmp_path / "data" / "runtime"))
    monkeypatch.setenv("PANEL_LOG_DIR", str(tmp_path / "data" / "logs"))
    monkeypatch.setenv("PANEL_ADMIN_TOKEN", "test-token")
    monkeypatch.setenv("PANEL_STATIC_DIR", str(tmp_path / "missing-static"))
    settings = importlib.import_module("olcrtc_panel.settings")
    importlib.reload(settings)
    module = importlib.import_module("olcrtc_panel.main")
    importlib.reload(module)
    return TestClient(module.create_app())


def auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer test-token"}


def test_requires_auth(tmp_path, monkeypatch):
    client = build_client(tmp_path, monkeypatch)
    response = client.get("/api/status")
    assert response.status_code == 401


def test_create_jitsi_profile_and_subscription(tmp_path, monkeypatch):
    client = build_client(tmp_path, monkeypatch)
    response = client.post(
        "/api/profiles",
        json={
            "user_name": "demo",
            "provider": "jitsi",
            "jitsi_server": "https://meet.example.org",
            "start_now": False,
        },
        headers=auth_headers(),
    )
    assert response.status_code == 200, response.text
    profile = response.json()
    assert profile["provider"] == "jitsi"
    assert profile["transport"] == "datachannel"
    assert profile["room_id"].startswith("https://meet.example.org/olcrtc-")
    assert profile["uri"].startswith("olcrtc://jitsi?datachannel@")
    assert profile["subscription_url"].startswith("/sub/")
    token = profile["subscription_url"].split("/sub/", 1)[1]
    sub = client.get(f"/sub/{token}")
    assert sub.status_code == 200
    assert "olcrtc://jitsi?datachannel@" in sub.text
