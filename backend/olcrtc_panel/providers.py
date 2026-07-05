"""Provider automation helpers for Jitsi and WBStream."""

from __future__ import annotations

import asyncio
import base64
import json
import re
import secrets
import time
from dataclasses import dataclass
from typing import Any

import httpx

JITSI_CANDIDATES = [
    "https://meet.jit.si",
    "https://jitsi.random-redirect.de",
    "https://meet.handyweb.org",
    "https://meet.small-dm.ru",
    "https://meet1.arbitr.ru",
    "https://fairmeeting.net",
    "https://calls.disroot.org",
    "https://meet.ffmuc.net",
    "https://meet.in-berlin.de",
    "https://meet.golem.de",
    "https://meet.systemli.org",
    "https://meet.adminforge.de",
    "https://meet.opensuse.org",
    "https://meet.jitsi.world",
    "https://jitsi.debian.social",
    "https://jitsi.hamburg.ccc.de",
    "https://jitsi.fem.tu-ilmenau.de",
    "https://jitsi.freifunk-duesseldorf.de",
    "https://jitsi.flyingcircus.io",
    "https://jitsi.ff3l.net",
    "https://freejitsi01.netcup.net",
    "https://jitsi.php-friends.de",
    "https://jitsi.nluug.nl",
    "https://jitsi.is",
    "https://meet.nerd.re",
    "https://meet.rexum.space",
    "https://meet.coredump.ch",
    "https://jitsi.projectsegfau.lt",
    "https://meet.guifi.net",
    "https://open.meet.switch.ch",
    "https://unibe.meet.switch.ch",
    "https://unifr.meet.switch.ch",
    "https://uzh.meet.switch.ch",
    "https://vc.autistici.org",
    "https://www.kuketz-meet.de",
]
JITSI_PROBE_CONCURRENCY = 8


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
                    if re.search(r"tokenAuthUrl|jwt|enableUserRolesBasedOnToken", resp.text, re.IGNORECASE):
                        result["requires_registration"] = True
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
    if result["requires_registration"]:
        result["ok"] = False
        result["status"] = "требует token/auth, не подходит для anonymous olcrtc"
    if not result["ok"]:
        result["status"] = result["status"] if result["requires_registration"] else "не отвечает"
    return result


async def discover_jitsi(candidates: list[str] | None = None) -> list[dict[str, Any]]:
    unique = []
    seen = set()
    for candidate in candidates or JITSI_CANDIDATES:
        try:
            base = normalize_jitsi_base(candidate)
        except ValueError:
            continue
        if base in seen:
            continue
        seen.add(base)
        unique.append(base)

    semaphore = asyncio.Semaphore(JITSI_PROBE_CONCURRENCY)

    async def run_probe(candidate: str) -> dict[str, Any]:
        async with semaphore:
            try:
                return await probe_jitsi_server(candidate)
            except Exception as exc:  # noqa: BLE001 - diagnostic text for UI.
                return {"url": candidate, "ok": False, "latency_ms": 0, "status": "ошибка проверки", "error": str(exc)}

    out = await asyncio.gather(*(run_probe(candidate) for candidate in unique))
    return sorted(out, key=lambda item: (not item["ok"], item.get("latency_ms", 999999)))


@dataclass
class WBCreateResult:
    room_id: str
    access_token: str
    endpoint: str
    raw: dict[str, Any]


class WBStreamAutomationError(RuntimeError):
    """Raised when WBStream room automation fails."""

    def __init__(self, attempts: list[dict[str, Any]], message: str = "WBStream room auto-create failed") -> None:
        self.attempts = attempts
        super().__init__(message)


def extract_room_id(data: Any) -> str:
    if isinstance(data, dict):
        for key in ("id", "roomId", "room_id", "roomID", "uuid", "hash", "code"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        for key in ("room", "roomInfo", "room_info", "data", "result"):
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


def jwt_payload(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(payload.encode("utf-8")))
    except Exception:  # noqa: BLE001 - invalid third-party token format.
        return {}
    return data if isinstance(data, dict) else {}


def wb_owner_id_from_token(token: str) -> str:
    payload = jwt_payload(token)
    user = payload.get("user")
    if isinstance(user, dict):
        value = user.get("userID") or user.get("userId") or user.get("id")
        if isinstance(value, str) and value.strip():
            return value.strip()
    value = payload.get("sub")
    return value.strip() if isinstance(value, str) else ""


def wb_room_payloads(title: str, owner_id: str) -> list[dict[str, Any]]:
    if not owner_id:
        return [{"title": title}, {"name": title}, {"displayName": title}, {"title": title, "isPublic": True}]
    room_info = {
        "ownerId": owner_id,
        "title": title,
        "roomType": 1,
        "roomPrivacy": 1,
    }
    return [
        {"roomInfo": room_info},
        {"roomInfo": {**room_info, "name": title}},
        {"roomInfo": {**room_info, "roomType": "ROOM_TYPE_WEBINAR"}},
    ]


async def create_wbstream_room(auth_token: str = "", title: str = "olcrtc") -> WBCreateResult:
    attempts: list[dict[str, Any]] = []
    async with httpx.AsyncClient(follow_redirects=True, timeout=12.0) as client:
        access_token = auth_token.strip() or await wb_guest_register(client, title)
        owner_id = wb_owner_id_from_token(access_token)
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Linux x86_64)",
        }
        payloads = wb_room_payloads(title, owner_id)
        endpoints = [
            "https://stream.wb.ru/api-room/api/v1/room",
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
    guest_forbidden = any("Guests are not allowed to create room" in str(item.get("body", "")) for item in attempts)
    if guest_forbidden and not auth_token.strip():
        raise WBStreamAutomationError(attempts, "WBStream больше не разрешает guest-token создавать room. Укажи account token или готовый Room ID.")
    if guest_forbidden:
        raise WBStreamAutomationError(attempts, "WBStream token не имеет прав на создание room.")
    raise WBStreamAutomationError(attempts)
