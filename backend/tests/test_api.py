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
    assert profile["profile_subscription_url"].startswith("/sub/")
    assert f"profile_id={profile['id']}" in profile["profile_subscription_url"]
    token = profile["subscription_url"].split("/sub/", 1)[1]
    sub = client.get(f"/sub/{token}")
    assert sub.status_code == 200
    assert "olcrtc://jitsi?datachannel@" in sub.text
    profile_sub = client.get(profile["profile_subscription_url"])
    assert profile_sub.status_code == 200
    assert "olcrtc://jitsi?datachannel@" in profile_sub.text


def test_profile_subscription_filters_single_profile(tmp_path, monkeypatch):
    client = build_client(tmp_path, monkeypatch)
    first = client.post(
        "/api/profiles",
        json={"user_name": "demo", "provider": "jitsi", "jitsi_server": "https://meet.example.org", "start_now": False},
        headers=auth_headers(),
    ).json()
    second = client.post(
        "/api/profiles",
        json={
            "user_id": first["user_id"],
            "name": "vp8-only",
            "provider": "jitsi",
            "transport": "vp8channel",
            "jitsi_server": "https://meet.example.org",
            "start_now": False,
        },
        headers=auth_headers(),
    ).json()

    shared_sub = client.get(first["subscription_url"])
    profile_sub = client.get(second["profile_subscription_url"])

    assert shared_sub.status_code == 200
    assert "olcrtc://jitsi?datachannel@" in shared_sub.text
    assert "olcrtc://jitsi?vp8channel" in shared_sub.text
    assert profile_sub.status_code == 200
    assert "olcrtc://jitsi?datachannel@" not in profile_sub.text
    assert "olcrtc://jitsi?vp8channel" in profile_sub.text


def test_delete_profile(tmp_path, monkeypatch):
    client = build_client(tmp_path, monkeypatch)
    created = client.post(
        "/api/profiles",
        json={"user_name": "demo", "provider": "jitsi", "jitsi_server": "https://meet.example.org", "start_now": False},
        headers=auth_headers(),
    ).json()

    response = client.delete(f"/api/profiles/{created['id']}", headers=auth_headers())
    status = client.get("/api/status", headers=auth_headers()).json()

    assert response.status_code == 200
    assert status["profiles"] == []
    assert len(status["users"]) == 1


def test_delete_user_cascades_profiles_and_subscription(tmp_path, monkeypatch):
    client = build_client(tmp_path, monkeypatch)
    created = client.post(
        "/api/profiles",
        json={"user_name": "demo", "provider": "jitsi", "jitsi_server": "https://meet.example.org", "start_now": False},
        headers=auth_headers(),
    ).json()
    user_id = created["user_id"]
    sub_token = created["subscription_url"].split("/sub/", 1)[1]

    response = client.delete(f"/api/users/{user_id}", headers=auth_headers())
    status = client.get("/api/status", headers=auth_headers()).json()
    sub = client.get(f"/sub/{sub_token}")

    assert response.status_code == 200
    assert status["users"] == []
    assert status["profiles"] == []
    assert sub.status_code == 404


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


def test_jitsi_probe_requires_anonymous_xmpp(monkeypatch):
    from olcrtc_panel import providers

    class FakeResponse:
        def __init__(self, status_code: int, text: str = "") -> None:
            self.status_code = status_code
            self.text = text

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            return None

        async def get(self, url: str):
            if url.endswith("/config.js"):
                return FakeResponse(200, "hosts: { domain: 'xmpp.example.org', muc: 'conference.xmpp.example.org' }")
            return FakeResponse(200)

        async def post(self, url: str, content: str, headers: dict[str, str]):
            assert "to='xmpp.example.org'" in content
            return FakeResponse(200, "<stream:features><mechanisms><mechanism>PLAIN</mechanism></mechanisms></stream:features>")

    monkeypatch.setattr(providers.httpx, "AsyncClient", FakeClient)

    result = asyncio.run(providers.probe_jitsi_server("https://meet.example.org"))

    assert result["ok"] is False
    assert result["requires_registration"] is True
    assert result["xmpp_anonymous"] is False
    assert result["status"] == "XMPP не разрешает anonymous login"


def test_jitsi_probe_accepts_anonymous_xmpp(monkeypatch):
    from olcrtc_panel import providers

    class FakeResponse:
        def __init__(self, status_code: int, text: str = "") -> None:
            self.status_code = status_code
            self.text = text

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            return None

        async def get(self, url: str):
            if url.endswith("/config.js"):
                return FakeResponse(200, "hosts: { domain: 'xmpp.example.org' }")
            return FakeResponse(200)

        async def post(self, url: str, content: str, headers: dict[str, str]):
            assert "to='xmpp.example.org'" in content
            return FakeResponse(200, "<mechanisms><mechanism>ANONYMOUS</mechanism></mechanisms>")

    monkeypatch.setattr(providers.httpx, "AsyncClient", FakeClient)

    result = asyncio.run(providers.probe_jitsi_server("https://meet.example.org"))

    assert result["ok"] is True
    assert result["xmpp_anonymous"] is True
    assert result["status"] == "config.js и anonymous XMPP доступны"


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
