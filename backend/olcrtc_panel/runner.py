"""Process supervisor for olcrtc srv profiles."""

from __future__ import annotations

import json
import os
import secrets
import signal
import subprocess
import threading
from pathlib import Path
from typing import Any

from . import settings
from .db import Store, now


def shell_string(value: str) -> str:
    return json.dumps(value)


def generate_key() -> str:
    return secrets.token_hex(32)


def profile_uri(profile: dict[str, Any]) -> str:
    transport = profile["transport"]
    payload = "<vp8-fps=30&vp8-batch=64>" if transport == "vp8channel" else ""
    comment = profile.get("name") or f"profile-{profile['id']}"
    return f"olcrtc://{profile['provider']}?{transport}{payload}@{profile['room_id']}#{profile['key_hex']}${comment}"


def render_server_yaml(profile: dict[str, Any]) -> str:
    lines = ["mode: srv", "auth:", f"  provider: {shell_string(profile['provider'])}"]
    if profile.get("auth_token"):
        lines.append(f"  token: {shell_string(profile['auth_token'])}")
    lines.extend(
        [
            "room:",
            f"  id: {shell_string(profile['room_id'])}",
            "crypto:",
            f"  key: {shell_string(profile['key_hex'])}",
            "net:",
            f"  transport: {shell_string(profile['transport'])}",
            f"  dns: {shell_string(profile['dns'])}",
            "liveness:",
            "  interval: 10s",
            "  timeout: 5s",
            "  failures: 3",
        ]
    )
    if profile["transport"] == "vp8channel":
        lines.extend(["vp8:", "  fps: 30", "  batch_size: 64"])
    lines.extend([f"data: {shell_string(settings.OLCRTC_DATA_DIR)}", "debug: true"])
    return "\n".join(lines) + "\n"


class RunnerManager:
    """Starts, stops, and restores olcrtc srv subprocesses."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self._lock = threading.RLock()
        self._processes: dict[int, subprocess.Popen[bytes]] = {}

    def start_autostart(self) -> None:
        for profile in self.store.list_profiles():
            if profile["enabled"] and profile["autostart"] and profile.get("user_enabled", 1):
                try:
                    self.start(profile["id"])
                except Exception as exc:  # noqa: BLE001 - startup must continue.
                    self.store.update_profile_state(profile["id"], "error", str(exc), "")
                    self.store.add_event("error", f"autostart failed: {exc}", profile["id"])

    def refresh(self, profile_id: int) -> dict[str, Any] | None:
        profile = self.store.get_profile(profile_id)
        if profile is None:
            return None
        proc = self._processes.get(profile_id)
        if proc is None:
            return profile
        code = proc.poll()
        if code is None:
            profile["status"] = "running"
            return profile
        self._processes.pop(profile_id, None)
        if code == 0:
            self.store.update_profile_state(profile_id, "stopped", "", "")
        else:
            self.store.update_profile_state(profile_id, "error", f"olcrtc exited with code {code}", "")
        return self.store.get_profile(profile_id)

    def refresh_all(self) -> None:
        for profile_id in list(self._processes):
            self.refresh(profile_id)

    def start(self, profile_id: int) -> dict[str, Any]:
        with self._lock:
            current = self.refresh(profile_id)
            if current is None:
                raise ValueError("profile not found")
            if not current["enabled"] or not current.get("user_enabled", 1):
                raise ValueError("profile or user is disabled")
            running = self._processes.get(profile_id)
            if running is not None and running.poll() is None:
                return current
            profile_dir = settings.RUNTIME_DIR / f"profile-{profile_id}"
            profile_dir.mkdir(parents=True, exist_ok=True)
            config_path = profile_dir / "server.yaml"
            config_path.write_text(render_server_yaml(current), encoding="utf-8")
            log_path = self.log_path(profile_id)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_file = log_path.open("ab")
            proc = subprocess.Popen(
                [settings.OLCRTC_BIN, str(config_path)],
                stdout=log_file,
                stderr=subprocess.STDOUT,
                cwd=str(profile_dir),
                env=os.environ.copy(),
                start_new_session=True,
            )
            self._processes[profile_id] = proc
            self.store.update_profile_state(profile_id, "running", "", now())
            self.store.add_event("info", "profile started", profile_id)
            return self.store.get_profile(profile_id) or current

    def stop(self, profile_id: int) -> None:
        with self._lock:
            proc = self._processes.pop(profile_id, None)
            if proc is None:
                self.store.update_profile_state(profile_id, "stopped", "", "")
                return
            if proc.poll() is None:
                try:
                    if os.name == "posix":
                        os.killpg(proc.pid, signal.SIGTERM)
                    else:
                        proc.terminate()
                    proc.wait(timeout=12)
                except Exception:
                    proc.kill()
            self.store.update_profile_state(profile_id, "stopped", "", "")
            self.store.add_event("info", "profile stopped", profile_id)

    def stop_all(self) -> None:
        for profile_id in list(self._processes):
            self.stop(profile_id)

    def log_path(self, profile_id: int) -> Path:
        return settings.LOG_DIR / f"profile-{profile_id}.log"

    def tail_log(self, profile_id: int, limit: int = 20000) -> str:
        path = self.log_path(profile_id)
        if not path.exists():
            return ""
        with path.open("rb") as file:
            file.seek(0, os.SEEK_END)
            size = file.tell()
            file.seek(max(0, size - limit), os.SEEK_SET)
            return file.read().decode("utf-8", errors="replace")
