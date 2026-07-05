"""Runtime settings for olcrtc-panel."""

from __future__ import annotations

import os
from pathlib import Path

VERSION = os.getenv("PANEL_VERSION", "0.1.0")


def env_path(name: str, default: str) -> Path:
    return Path(os.getenv(name, default)).expanduser().resolve()


DATA_DIR = env_path("PANEL_DATA_DIR", "/data")
DB_PATH = env_path("PANEL_DB_PATH", str(DATA_DIR / "panel.db"))
RUNTIME_DIR = env_path("PANEL_RUNTIME_DIR", str(DATA_DIR / "runtime"))
LOG_DIR = env_path("PANEL_LOG_DIR", str(DATA_DIR / "logs"))
BACKUP_DIR = env_path("PANEL_BACKUP_DIR", str(DATA_DIR / "backups"))
STATIC_DIR = env_path("PANEL_STATIC_DIR", "/app/static")

ADMIN_TOKEN = os.getenv("PANEL_ADMIN_TOKEN", "change-me")
PUBLIC_BASE_URL = os.getenv("PANEL_PUBLIC_BASE_URL", "").rstrip("/")
PANEL_PATH = os.getenv("PANEL_PATH", "/panel")

OLCRTC_BIN = os.getenv("OLCRTC_BIN", "/usr/local/bin/olcrtc")
OLCRTC_DATA_DIR = os.getenv("OLCRTC_DATA_DIR", "/opt/olcrtc-data")

DEFAULT_DNS = os.getenv("OLCRTC_DEFAULT_DNS", "8.8.8.8:53")
DEFAULT_JITSI = os.getenv("OLCRTC_DEFAULT_JITSI", "https://fairmeeting.net")


def ensure_dirs() -> None:
    for path in (DATA_DIR, RUNTIME_DIR, LOG_DIR, BACKUP_DIR):
        path.mkdir(parents=True, exist_ok=True)
