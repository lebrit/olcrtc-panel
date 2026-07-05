"""SQLite storage for panel state."""

from __future__ import annotations

import secrets
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  expires_at TEXT NOT NULL DEFAULT '',
  traffic_limit_mb INTEGER NOT NULL DEFAULT 0,
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS profiles (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  provider TEXT NOT NULL,
  transport TEXT NOT NULL,
  room_id TEXT NOT NULL,
  key_hex TEXT NOT NULL,
  channel_id TEXT NOT NULL DEFAULT '',
  auth_token TEXT NOT NULL DEFAULT '',
  dns TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  autostart INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'stopped',
  last_error TEXT NOT NULL DEFAULT '',
  started_at TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS subscriptions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  expires_at TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  profile_id INTEGER NOT NULL DEFAULT 0,
  level TEXT NOT NULL,
  message TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_profiles_user_id ON profiles(user_id);
CREATE INDEX IF NOT EXISTS idx_subscriptions_token ON subscriptions(token);
"""


def now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


class Store:
    """Small explicit SQLite wrapper."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def init(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    def list_users(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM users ORDER BY id DESC").fetchall()
        return [row_to_dict(row) or {} for row in rows]

    def get_user(self, user_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return row_to_dict(row)

    def create_user(self, name: str, expires_at: str = "", traffic_limit_mb: int = 0, note: str = "") -> dict[str, Any]:
        stamp = now()
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO users (name, expires_at, traffic_limit_mb, note, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (name, expires_at, traffic_limit_mb, note, stamp, stamp),
            )
            user_id = int(cur.lastrowid)
        self.ensure_subscription(user_id, name)
        user = self.get_user(user_id)
        if user is None:
            raise RuntimeError("created user not found")
        return user

    def set_user_enabled(self, user_id: int, enabled: bool) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE users SET enabled=?, updated_at=? WHERE id=?", (1 if enabled else 0, now(), user_id))

    def list_profiles(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT p.*, u.name AS user_name, u.enabled AS user_enabled
                FROM profiles p JOIN users u ON u.id = p.user_id
                ORDER BY p.id DESC
                """
            ).fetchall()
        return [row_to_dict(row) or {} for row in rows]

    def list_enabled_profiles_for_user(self, user_id: int) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT p.*, u.name AS user_name
                FROM profiles p JOIN users u ON u.id = p.user_id
                WHERE p.user_id=? AND p.enabled=1 AND u.enabled=1
                ORDER BY p.id ASC
                """,
                (user_id,),
            ).fetchall()
        return [row_to_dict(row) or {} for row in rows]

    def get_profile(self, profile_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT p.*, u.name AS user_name, u.enabled AS user_enabled
                FROM profiles p JOIN users u ON u.id = p.user_id
                WHERE p.id=?
                """,
                (profile_id,),
            ).fetchone()
        return row_to_dict(row)

    def create_profile(self, values: dict[str, Any]) -> dict[str, Any]:
        stamp = now()
        data = {
            "user_id": values["user_id"],
            "name": values["name"],
            "provider": values["provider"],
            "transport": values["transport"],
            "room_id": values["room_id"],
            "key_hex": values["key_hex"],
            "channel_id": values.get("channel_id", ""),
            "auth_token": values.get("auth_token", ""),
            "dns": values["dns"],
            "enabled": 1 if values.get("enabled", True) else 0,
            "autostart": 1 if values.get("autostart", True) else 0,
            "created_at": stamp,
            "updated_at": stamp,
        }
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO profiles (
                  user_id, name, provider, transport, room_id, key_hex, channel_id,
                  auth_token, dns, enabled, autostart, created_at, updated_at
                ) VALUES (
                  :user_id, :name, :provider, :transport, :room_id, :key_hex, :channel_id,
                  :auth_token, :dns, :enabled, :autostart, :created_at, :updated_at
                )
                """,
                data,
            )
            profile_id = int(cur.lastrowid)
        profile = self.get_profile(profile_id)
        if profile is None:
            raise RuntimeError("created profile not found")
        return profile

    def update_profile_state(self, profile_id: int, status: str, last_error: str = "", started_at: str = "") -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE profiles SET status=?, last_error=?, started_at=?, updated_at=? WHERE id=?",
                (status, last_error, started_at, now(), profile_id),
            )

    def update_profile_key(self, profile_id: int, key_hex: str) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE profiles SET key_hex=?, updated_at=? WHERE id=?", (key_hex, now(), profile_id))

    def delete_profile(self, profile_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM profiles WHERE id=?", (profile_id,))

    def ensure_subscription(self, user_id: int, name: str = "") -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM subscriptions WHERE user_id=? AND enabled=1 ORDER BY id ASC LIMIT 1",
                (user_id,),
            ).fetchone()
            if row is not None:
                return row_to_dict(row) or {}
            stamp = now()
            token = secrets.token_urlsafe(32)
            cur = conn.execute(
                "INSERT INTO subscriptions (user_id, token, name, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, token, name or f"user-{user_id}", stamp, stamp),
            )
            row = conn.execute("SELECT * FROM subscriptions WHERE id=?", (int(cur.lastrowid),)).fetchone()
        return row_to_dict(row) or {}

    def get_subscription(self, token: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT s.*, u.enabled AS user_enabled
                FROM subscriptions s JOIN users u ON u.id = s.user_id
                WHERE s.token=?
                """,
                (token,),
            ).fetchone()
        return row_to_dict(row)

    def add_event(self, level: str, message: str, profile_id: int = 0) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO events (profile_id, level, message, created_at) VALUES (?, ?, ?, ?)",
                (profile_id, level, message, now()),
            )

    def list_events(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [row_to_dict(row) or {} for row in rows]
