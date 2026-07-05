from __future__ import annotations

import asyncio
import base64
import json
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


def test_jitsi_discovery_deduplicates_and_sorts(monkeypatch):
    from olcrtc_panel import providers

    async def fake_probe(candidate: str):
        return {
            "url": candidate,
            "ok": candidate.endswith("ok.example"),
            "latency_ms": 10 if candidate.endswith("ok.example") else 500,
            "status": "test",
        }

    monkeypatch.setattr(providers, "probe_jitsi_server", fake_probe)
    result = asyncio.run(providers.discover_jitsi(["ok.example", "https://ok.example", "https://slow.example"]))

    assert [item["url"] for item in result] == ["https://ok.example", "https://slow.example"]


def test_wbstream_room_payload_uses_owner_id_from_jwt():
    from olcrtc_panel import providers

    payload = base64.urlsafe_b64encode(json.dumps({"sub": "owner-1", "user": {"userID": "owner-2"}}).encode()).decode().rstrip("=")
    token = f"header.{payload}.sig"

    owner_id = providers.wb_owner_id_from_token(token)
    room_payload = providers.wb_room_payloads("demo", owner_id)[0]

    assert owner_id == "owner-2"
    assert room_payload["roomInfo"]["ownerId"] == "owner-2"
    assert room_payload["roomInfo"]["title"] == "demo"
    assert room_payload["roomInfo"]["roomType"] == 1
    assert room_payload["roomInfo"]["roomPrivacy"] == 1
    assert providers.extract_room_id({"roomInfo": {"uuid": "room-123"}}) == "room-123"
