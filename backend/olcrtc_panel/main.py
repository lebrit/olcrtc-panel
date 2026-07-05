"""FastAPI application for olcrtc-panel."""

from __future__ import annotations

import secrets
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import settings
from .db import Store
from .providers import (
    JITSI_CANDIDATES,
    WBStreamAutomationError,
    create_wbstream_room,
    discover_jitsi,
    generate_jitsi_room,
    normalize_jitsi_base,
)
from .runner import RunnerManager, generate_key, profile_uri


class UserCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    expires_at: str = ""
    traffic_limit_mb: int = 0
    note: str = ""


class ProfileCreate(BaseModel):
    user_id: int | None = None
    user_name: str = "Пользователь"
    name: str = ""
    provider: str = "jitsi"
    transport: str = ""
    room_id: str = ""
    key_hex: str = ""
    channel_id: str = ""
    auth_token: str = ""
    dns: str = settings.DEFAULT_DNS
    jitsi_server: str = settings.DEFAULT_JITSI
    autostart: bool = True
    start_now: bool = True
    auto_wbstream_room: bool = False


class JitsiDiscoverRequest(BaseModel):
    candidates: list[str] = Field(default_factory=list)


class WBStreamCreateRoomRequest(BaseModel):
    auth_token: str = ""
    title: str = "olcrtc"


def build_store() -> Store:
    settings.ensure_dirs()
    return Store(settings.DB_PATH)


def require_auth(authorization: str = Header(default="")) -> None:
    if settings.ADMIN_TOKEN and authorization != f"Bearer {settings.ADMIN_TOKEN}":
        raise HTTPException(status_code=401, detail="unauthorized")


def subscription_url(token: str, profile_id: int | None = None) -> str:
    if settings.PUBLIC_BASE_URL:
        url = f"{settings.PUBLIC_BASE_URL}/sub/{token}"
    else:
        url = f"/sub/{token}"
    if profile_id is not None:
        return f"{url}?profile_id={profile_id}"
    return url


def decorate_profile(profile: dict[str, Any], store: Store) -> dict[str, Any]:
    item = dict(profile)
    item["uri"] = profile_uri(item)
    sub = store.ensure_subscription(int(item["user_id"]), item.get("user_name") or item["name"])
    item["subscription_url"] = subscription_url(sub["token"])
    item["profile_subscription_url"] = subscription_url(sub["token"], int(item["id"]))
    return item


def create_app() -> FastAPI:
    store = build_store()
    runner = RunnerManager(store)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        runner.start_autostart()
        try:
            yield
        finally:
            runner.stop_all()

    app = FastAPI(title="olcrtc-panel", version=settings.VERSION, lifespan=lifespan)

    @app.get("/api/bootstrap")
    def bootstrap() -> dict[str, Any]:
        return {
            "version": settings.VERSION,
            "panel_path": settings.PANEL_PATH,
            "default_jitsi": settings.DEFAULT_JITSI,
            "jitsi_candidates": JITSI_CANDIDATES,
            "auth_required": bool(settings.ADMIN_TOKEN),
        }

    @app.get("/api/status", dependencies=[Depends(require_auth)])
    def status() -> dict[str, Any]:
        runner.refresh_all()
        profiles = [decorate_profile(profile, store) for profile in store.list_profiles()]
        return {"version": settings.VERSION, "profiles": profiles, "users": store.list_users(), "running": sum(1 for p in profiles if p["status"] == "running")}

    @app.post("/api/users", dependencies=[Depends(require_auth)])
    def create_user(payload: UserCreate) -> dict[str, Any]:
        user = store.create_user(payload.name.strip(), payload.expires_at.strip(), payload.traffic_limit_mb, payload.note.strip())
        store.add_event("info", f"user created: {user['name']}")
        return user

    @app.post("/api/users/{user_id}/enable", dependencies=[Depends(require_auth)])
    def enable_user(user_id: int) -> dict[str, str]:
        store.set_user_enabled(user_id, True)
        return {"status": "ok"}

    @app.post("/api/users/{user_id}/disable", dependencies=[Depends(require_auth)])
    def disable_user(user_id: int) -> dict[str, str]:
        for profile in store.list_enabled_profiles_for_user(user_id):
            runner.stop(int(profile["id"]))
        store.set_user_enabled(user_id, False)
        return {"status": "ok"}

    @app.delete("/api/users/{user_id}", dependencies=[Depends(require_auth)])
    def delete_user(user_id: int) -> dict[str, str]:
        user = store.get_user(user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="user not found")
        for profile in store.list_profiles_for_user(user_id):
            runner.stop(int(profile["id"]))
        store.delete_user(user_id)
        store.add_event("info", f"user deleted: {user['name']}")
        return {"status": "ok"}

    @app.get("/api/users/{user_id}/subscription", dependencies=[Depends(require_auth)])
    def user_subscription(user_id: int) -> dict[str, Any]:
        user = store.get_user(user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="user not found")
        sub = store.ensure_subscription(user_id, user["name"])
        return {**sub, "url": subscription_url(sub["token"])}

    @app.get("/api/profiles", dependencies=[Depends(require_auth)])
    def profiles() -> list[dict[str, Any]]:
        runner.refresh_all()
        return [decorate_profile(profile, store) for profile in store.list_profiles()]

    @app.post("/api/profiles", dependencies=[Depends(require_auth)])
    async def create_profile(payload: ProfileCreate) -> dict[str, Any]:
        user_id = payload.user_id
        if user_id is None:
            user_id = int(store.create_user(payload.user_name.strip() or "Пользователь")["id"])
        elif store.get_user(user_id) is None:
            raise HTTPException(status_code=404, detail="user not found")
        provider = payload.provider.strip().lower()
        if provider not in {"jitsi", "wbstream"}:
            raise HTTPException(status_code=400, detail="provider must be jitsi or wbstream")
        transport = payload.transport.strip().lower() or ("datachannel" if provider == "jitsi" else "vp8channel")
        room_id = payload.room_id.strip()
        auth_token = payload.auth_token.strip()
        if provider == "jitsi":
            if not room_id:
                room_id = generate_jitsi_room(payload.jitsi_server)
            elif "/" not in room_id:
                room_id = f"{normalize_jitsi_base(payload.jitsi_server)}/{room_id}"
        elif payload.auto_wbstream_room:
            try:
                created = await create_wbstream_room(auth_token, payload.name or "olcrtc")
            except WBStreamAutomationError as exc:
                raise HTTPException(status_code=424, detail={"message": str(exc), "attempts": exc.attempts}) from exc
            room_id = created.room_id
            auth_token = auth_token or created.access_token
        elif not room_id:
            raise HTTPException(status_code=400, detail="room_id required for wbstream without automation")
        profile = store.create_profile(
            {
                "user_id": user_id,
                "name": payload.name.strip() or f"{provider}-{user_id}",
                "provider": provider,
                "transport": transport,
                "room_id": room_id,
                "key_hex": payload.key_hex.strip() or generate_key(),
                "channel_id": payload.channel_id.strip() or secrets.token_hex(4),
                "auth_token": auth_token,
                "dns": payload.dns.strip() or settings.DEFAULT_DNS,
                "autostart": payload.autostart,
            }
        )
        if payload.start_now:
            try:
                profile = runner.start(int(profile["id"]))
            except Exception as exc:  # noqa: BLE001 - shown in panel.
                store.update_profile_state(int(profile["id"]), "error", str(exc), "")
                store.add_event("error", f"start failed: {exc}", int(profile["id"]))
                profile = store.get_profile(int(profile["id"])) or profile
        return decorate_profile(profile, store)

    @app.post("/api/profiles/{profile_id}/start", dependencies=[Depends(require_auth)])
    def start_profile(profile_id: int) -> dict[str, Any]:
        return decorate_profile(runner.start(profile_id), store)

    @app.post("/api/profiles/{profile_id}/stop", dependencies=[Depends(require_auth)])
    def stop_profile(profile_id: int) -> dict[str, str]:
        runner.stop(profile_id)
        return {"status": "ok"}

    @app.post("/api/profiles/{profile_id}/rotate-key", dependencies=[Depends(require_auth)])
    def rotate_key(profile_id: int) -> dict[str, Any]:
        runner.stop(profile_id)
        store.update_profile_key(profile_id, generate_key())
        profile = store.get_profile(profile_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="profile not found")
        if profile["enabled"] and profile["autostart"]:
            profile = runner.start(profile_id)
        return decorate_profile(profile, store)

    @app.delete("/api/profiles/{profile_id}", dependencies=[Depends(require_auth)])
    def delete_profile(profile_id: int) -> dict[str, str]:
        if store.get_profile(profile_id) is None:
            raise HTTPException(status_code=404, detail="profile not found")
        runner.stop(profile_id)
        store.delete_profile(profile_id)
        return {"status": "ok"}

    @app.get("/api/profiles/{profile_id}/logs", dependencies=[Depends(require_auth)])
    def profile_logs(profile_id: int) -> PlainTextResponse:
        if store.get_profile(profile_id) is None:
            raise HTTPException(status_code=404, detail="profile not found")
        return PlainTextResponse(runner.tail_log(profile_id))

    @app.post("/api/jitsi/discover", dependencies=[Depends(require_auth)])
    async def jitsi_discover(payload: JitsiDiscoverRequest) -> list[dict[str, Any]]:
        return await discover_jitsi(payload.candidates or None)

    @app.post("/api/wbstream/create-room", dependencies=[Depends(require_auth)])
    async def wbstream_create_room(payload: WBStreamCreateRoomRequest) -> dict[str, Any]:
        try:
            result = await create_wbstream_room(payload.auth_token, payload.title)
        except WBStreamAutomationError as exc:
            raise HTTPException(status_code=424, detail={"message": str(exc), "attempts": exc.attempts}) from exc
        return {"room_id": result.room_id, "auth_token": result.access_token, "endpoint": result.endpoint, "raw": result.raw}

    @app.get("/api/events", dependencies=[Depends(require_auth)])
    def events() -> list[dict[str, Any]]:
        return store.list_events()

    @app.get("/sub/{token}", response_class=PlainTextResponse)
    def subscription(token: str, profile_id: int | None = None) -> str:
        sub = store.get_subscription(token)
        if sub is None or not sub["enabled"] or not sub["user_enabled"]:
            raise HTTPException(status_code=404, detail="subscription not found")
        profiles = store.list_enabled_profiles_for_user(int(sub["user_id"]))
        if profile_id is not None:
            profiles = [profile for profile in profiles if int(profile["id"]) == profile_id]
            if not profiles:
                raise HTTPException(status_code=404, detail="profile not found in subscription")
        name = sub["name"] if profile_id is None else f"{sub['name']} / {profiles[0]['name']}"
        lines = [f"#name: {name}", f"#update: {int(time.time())}", "#refresh: 10m", "#color: #2563eb", "#icon: olcrtc", ""]
        for profile in profiles:
            lines.extend([profile_uri(profile), f"##name: {profile['name']}", f"##comment: {profile['provider']} / {profile['transport']}", ""])
        return "\n".join(lines)

    if settings.STATIC_DIR.exists():
        app.mount("/", StaticFiles(directory=settings.STATIC_DIR, html=True), name="static")
    return app


app = create_app()
