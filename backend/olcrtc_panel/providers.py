"""Provider automation helpers for Jitsi and WBStream."""

from __future__ import annotations

import re
import secrets
import time
from dataclasses import dataclass
from typing import Any

import httpx

JITSI_CANDIDATES = [
    "https://meet.handyweb.org",
    "https://meet.small-dm.ru",
    "https://meet1.arbitr.ru",
    "https://meet.jit.si",
]


def normalize_jitsi_base(raw: str) -> str:
    value = raw.strip()
    if not value:
        raise ValueError("empty Jitsi server")
    if "://" not in value:
        value = "https://" + value
    return value.rstrip("/")


def generate_jitsi_room(base_url: str, prefix: str = "olcrtc") -> str:
    return f"{normalize_jitsi_base(base_url)}/{prefix}-{secrets.token_hex(5)}"


async def probe_jitsi_server(base_url: str, timeout: float = 5.0) -> dict[str, Any]:
    base = normalize_jitsi_base(base_url)
    started = time.perf_counter()
    result: dict[str, Any] = {
        "url": base,
        "ok": False,
        "latency_ms": 0,
        "status": "unknown",
        "checks": [],
        "requires_registration": False,
    }
    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
        for path in ("/", "/config.js", "/http-bind"):
            check = {"path": path, "ok": False, "status_code": 0, "error": ""}
            try:
                resp = await client.get(base + path)
                check["status_code"] = resp.status_code
                check["ok"] = resp.status_code < 500
                if resp.status_code in (401, 403):
                    result["requires_registration"] = True
                if path == "/config.js" and resp.status_code == 200:
                    result["ok"] = True
                    result["status"] = "config.js доступен"
                elif path == "/" and resp.status_code < 400 and not result["ok"]:
                    result["status"] = "главная страница доступна"
            except Exception as exc:  # noqa: BLE001 - diagnostic text for UI.
                check["error"] = str(exc)
            result["checks"].append(check)
    result["latency_ms"] = int((time.perf_counter() - started) * 1000)
    if not result["ok"] and any(item["ok"] for item in result["checks"]):
        result["ok"] = True
        result["status"] = "сервер отвечает, нужен ручной тест комнаты"
    if not result["ok"]:
        result["status"] = "не отвечает"
    return result


async def discover_jitsi(candidates: list[str] | None = None) -> list[dict[str, Any]]:
    out = []
    for candidate in candidates or JITSI_CANDIDATES:
        try:
            out.append(await probe_jitsi_server(candidate))
        except Exception as exc:  # noqa: BLE001 - diagnostic text for UI.
            out.append({"url": candidate, "ok": False, "latency_ms": 0, "status": "ошибка проверки", "error": str(exc)})
    return sorted(out, key=lambda item: (not item["ok"], item.get("latency_ms", 999999)))


@dataclass
class WBCreateResult:
    room_id: str
    access_token: str
    endpoint: str
    raw: dict[str, Any]


class WBStreamAutomationError(RuntimeError):
    """Raised when WBStream room automation fails."""

    def __init__(self, attempts: list[dict[str, Any]]) -> None:
        self.attempts = attempts
        super().__init__("WBStream room auto-create failed")


def extract_room_id(data: Any) -> str:
    if isinstance(data, dict):
        for key in ("id", "roomId", "room_id", "roomID", "hash", "code"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        for key in ("room", "data", "result"):
            nested = extract_room_id(data.get(key))
            if nested:
                return nested
        for key in ("url", "link", "roomUrl"):
            value = data.get(key)
            if isinstance(value, str):
                match = re.search(r"/([A-Za-z0-9_-]{6,})(?:[/?#].*)?$", value)
                if match:
                    return match.group(1)
    if isinstance(data, list):
        for item in data:
            nested = extract_room_id(item)
            if nested:
                return nested
    return ""


async def wb_guest_register(client: httpx.AsyncClient, display_name: str) -> str:
    resp = await client.post(
        "https://stream.wb.ru/auth/api/v1/auth/user/guest-register",
        json={
            "displayName": display_name,
            "device": {"deviceName": "Linux", "deviceType": "PARTICIPANT_DEVICE_TYPE_WEB_DESKTOP"},
        },
        headers={"User-Agent": "Mozilla/5.0 (Linux x86_64)"},
    )
    resp.raise_for_status()
    token = resp.json().get("accessToken", "")
    if not token:
        raise RuntimeError("guest-register did not return accessToken")
    return token


async def create_wbstream_room(auth_token: str = "", title: str = "olcrtc") -> WBCreateResult:
    attempts: list[dict[str, Any]] = []
    async with httpx.AsyncClient(follow_redirects=True, timeout=12.0) as client:
        access_token = auth_token.strip() or await wb_guest_register(client, title)
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Linux x86_64)",
        }
        payloads = [{"title": title}, {"name": title}, {"displayName": title}, {"title": title, "isPublic": True}]
        endpoints = [
            "https://stream.wb.ru/api-room/api/v1/room",
            "https://stream.wb.ru/api-room/api/v1/room/create",
            "https://stream.wb.ru/api-room-manager/v1/room",
            "https://stream.wb.ru/api-room-manager/v2/room",
        ]
        for endpoint in endpoints:
            for payload in payloads:
                attempt = {"endpoint": endpoint, "payload": payload, "status_code": 0, "error": ""}
                try:
                    resp = await client.post(endpoint, json=payload, headers=headers)
                    attempt["status_code"] = resp.status_code
                    attempt["body"] = resp.text[:1200]
                    if resp.status_code < 400:
                        data = resp.json()
                        room_id = extract_room_id(data)
                        if room_id:
                            return WBCreateResult(room_id, access_token, endpoint, data)
                except Exception as exc:  # noqa: BLE001 - diagnostic text for UI.
                    attempt["error"] = str(exc)
                attempts.append(attempt)
    raise WBStreamAutomationError(attempts)
