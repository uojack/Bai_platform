#!/usr/bin/env python3
"""BaiPlayer 1.0 local AOS gateway and front-end runtime.

The default profile is deliberately safe: localhost only, simulated endpoints,
and GET-only AOS discovery. Device output is never enabled implicitly.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import queue
import re
import secrets
import sqlite3
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from adapters.huawei_dlna import HuaweiDLNAAdapter
from inventory import initial_inventory


ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
RUNTIME_ROOT = ROOT / "runtime"
DB_PATH = RUNTIME_ROOT / "baiplayer.sqlite3"
VERSION = "1.4.1"
MAX_BODY_BYTES = 2 * 1024 * 1024
FIELD_TRIGGER_MIN_CONFIDENCE = 0.80
FIELD_TRIGGER_COOLDOWN_SECONDS = 8
ALLOWED_COMMAND_TYPES = {"play_scene", "set_volume", "set_power_mode", "set_input_source", "restore_state"}
RAW_SENSOR_KEYS = {"rawAudio", "rawVideo", "recording", "frame", "image", "audioBytes", "videoBytes"}
SESSION_TTL_SECONDS = 12 * 60 * 60
ROLE_LEVEL = {"viewer": 0, "operator": 1, "admin": 2}


def password_hash(password: str, salt: bytes | None = None) -> str:
    if len(password) < 10:
        raise ValueError("password must contain at least 10 characters")
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310_000)
    return f"pbkdf2_sha256$310000${base64.urlsafe_b64encode(salt).decode()}${base64.urlsafe_b64encode(digest).decode()}"


def password_matches(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt_value, expected = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(salt_value)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(rounds))
        return hmac.compare_digest(base64.urlsafe_b64encode(digest).decode(), expected)
    except (ValueError, TypeError):
        return False


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


@dataclass
class RuntimeConfig:
    bind: str = "127.0.0.1"
    port: int = 4340
    aos_base_url: str = ""
    api_token: str = ""
    allow_simulation_writes: bool = True
    real_device_output: bool = False
    public_base_url: str = ""

    @property
    def mode(self) -> str:
        return "SIMULATION" if not self.real_device_output else "FIELD_GATED"


class EventBroker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: dict[str, list[queue.Queue[dict[str, Any]]]] = {}

    def subscribe(self, channel: str) -> queue.Queue[dict[str, Any]]:
        target: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=128)
        with self._lock:
            self._subscribers.setdefault(channel, []).append(target)
        return target

    def unsubscribe(self, channel: str, target: queue.Queue[dict[str, Any]]) -> None:
        with self._lock:
            subscribers = self._subscribers.get(channel, [])
            if target in subscribers:
                subscribers.remove(target)

    def publish(self, channel: str, event: dict[str, Any]) -> None:
        with self._lock:
            targets = list(self._subscribers.get(channel, [])) + list(self._subscribers.get("*", []))
        for target in targets:
            try:
                target.put_nowait(event)
            except queue.Full:
                try:
                    target.get_nowait()
                    target.put_nowait(event)
                except queue.Empty:
                    pass


class ClosingConnection(sqlite3.Connection):
    """Commit or roll back like sqlite3, then close deterministically."""

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> bool:
        result = super().__exit__(exc_type, exc_value, traceback)
        self.close()
        return result


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        self._setup()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=8, factory=ClosingConnection)
        connection.row_factory = sqlite3.Row
        return connection

    def _setup(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS devices (
                  id TEXT PRIMARY KEY, name TEXT NOT NULL, type TEXT NOT NULL,
                  zone_id TEXT NOT NULL, address TEXT NOT NULL DEFAULT '',
                  status TEXT NOT NULL DEFAULT 'offline', capabilities TEXT NOT NULL,
                  current_scene TEXT NOT NULL DEFAULT '', volume INTEGER NOT NULL DEFAULT 35,
                  last_seen TEXT NOT NULL, simulated INTEGER NOT NULL DEFAULT 1,
                  power_mode TEXT NOT NULL DEFAULT 'active'
                );
                CREATE TABLE IF NOT EXISTS scenes (
                  id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
                  accent TEXT NOT NULL, title TEXT NOT NULL, subtitle TEXT NOT NULL,
                  media_url TEXT NOT NULL DEFAULT '', duration INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS events (
                  id TEXT PRIMARY KEY, occurred_at TEXT NOT NULL, source TEXT NOT NULL,
                  event_type TEXT NOT NULL, zone_id TEXT NOT NULL DEFAULT '',
                  subject_id TEXT NOT NULL DEFAULT '', confidence REAL NOT NULL DEFAULT 1,
                  payload TEXT NOT NULL, result TEXT NOT NULL DEFAULT 'accepted'
                );
                CREATE TABLE IF NOT EXISTS commands (
                  id TEXT PRIMARY KEY, created_at TEXT NOT NULL, command_type TEXT NOT NULL,
                  target TEXT NOT NULL, scene_id TEXT NOT NULL DEFAULT '',
                  payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued'
                );
                CREATE TABLE IF NOT EXISTS command_acks (
                  command_id TEXT NOT NULL, device_id TEXT NOT NULL, status TEXT NOT NULL,
                  acknowledged_at TEXT NOT NULL,
                  PRIMARY KEY(command_id, device_id),
                  FOREIGN KEY(command_id) REFERENCES commands(id)
                );
                CREATE TABLE IF NOT EXISTS deployments (
                  id TEXT PRIMARY KEY, created_at TEXT NOT NULL, scene_id TEXT NOT NULL,
                  target_group TEXT NOT NULL DEFAULT '', targets TEXT NOT NULL,
                  snapshot TEXT NOT NULL, command_id TEXT NOT NULL DEFAULT '',
                  status TEXT NOT NULL DEFAULT 'prepared', restored_at TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS users (
                  id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL,
                  password_hash TEXT NOT NULL, role TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
                  must_change_password INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                  token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, created_at INTEGER NOT NULL,
                  expires_at INTEGER NOT NULL, FOREIGN KEY(user_id) REFERENCES users(id)
                );
                """
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(devices)")}
            migrations = {
                "power_mode": "TEXT NOT NULL DEFAULT 'active'", "category": "TEXT NOT NULL DEFAULT 'signage'",
                "model": "TEXT NOT NULL DEFAULT ''", "os_name": "TEXT NOT NULL DEFAULT ''",
                "power_supply": "TEXT NOT NULL DEFAULT 'unknown'", "resolution": "TEXT NOT NULL DEFAULT ''",
                "input_sources": "TEXT NOT NULL DEFAULT '[]'", "control_status": "TEXT NOT NULL DEFAULT '{}'",
                "audio_status": "TEXT NOT NULL DEFAULT 'pending_verification'", "adapter": "TEXT NOT NULL DEFAULT ''",
                "coord_x": "REAL", "coord_y": "REAL", "coord_z": "REAL",
                "coordinate_ref": "TEXT NOT NULL DEFAULT '20F-plan-v1'",
                "position_verified": "INTEGER NOT NULL DEFAULT 0", "address_verified": "INTEGER NOT NULL DEFAULT 0",
                "notes": "TEXT NOT NULL DEFAULT ''", "evidence": "TEXT NOT NULL DEFAULT ''",
                "input_source": "TEXT NOT NULL DEFAULT 'BaiPlayer'", "updated_at": "TEXT NOT NULL DEFAULT ''",
                "mac_address": "TEXT NOT NULL DEFAULT ''", "network_mode": "TEXT NOT NULL DEFAULT ''",
                "subnet_mask": "TEXT NOT NULL DEFAULT ''", "gateway": "TEXT NOT NULL DEFAULT ''",
                "dns_servers": "TEXT NOT NULL DEFAULT '[]'",
                "functional_group": "TEXT NOT NULL DEFAULT '未分组'",
            }
            for name, definition in migrations.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE devices ADD COLUMN {name} {definition}")
            if not db.execute("SELECT 1 FROM devices LIMIT 1").fetchone():
                self._seed(db)
            db.execute(
                "INSERT OR IGNORE INTO scenes VALUES(?,?,?,?,?,?,?,?)",
                ("huawei-connect-test", "华为电视连接测试", "image", "#39c8ee", "华为电视连接测试", "BaiPlayer · UPnP/DLNA", "/media/huawei-connect-test.png", 0),
            )
            self._sync_inventory(db)
            self._ensure_initial_admin(db)
            db.execute("UPDATE scenes SET name='1号弧讲解',subtitle='1号弧 · 自适应声场演示' WHERE id='product-a'")
            db.execute("UPDATE scenes SET name='2—4号弧讲解',subtitle='2—4号弧 · 一体化体验' WHERE id='product-b'")
            db.execute("DELETE FROM sessions WHERE expires_at < ?", (int(time.time()),))

    def _sync_inventory(self, db: sqlite3.Connection) -> None:
        catalog = initial_inventory()
        catalog_ids = {item["id"] for item in catalog}
        legacy_ids = [row[0] for row in db.execute("SELECT id FROM devices WHERE simulated=1")
                      if row[0] not in catalog_ids and re.fullmatch(r"(?:android|huawei)-\d+|bo-[ab]-\d+|apple-tv-01|audio-entry", row[0])]
        for device_id in legacy_ids:
            db.execute("DELETE FROM devices WHERE id=?", (device_id,))
        for item in catalog:
            existing = db.execute("SELECT name,zone_id,notes,coord_x,coord_y,coord_z,position_verified FROM devices WHERE id=?", (item["id"],)).fetchone()
            renamed_defaults = {
                "signage-14": {"产学研会议室信息发布屏", "产学研会议室 01号竖屏"},
                "signage-15": {"电梯厅信息发布屏"},
                "signage-16": {"20F 电梯厅信息发布屏"},
                "signage-28": {"研究前沿左竖屏", "产学研会议室 02号竖屏"},
                "signage-29": {"研究前沿中竖屏", "产学研会议室 03号竖屏"},
                "signage-30": {"研究前沿右竖屏", "产学研会议室 04号竖屏"},
            }
            keep_existing_name = (
                existing and existing["name"]
                and not existing["name"].startswith(("广告屏", "华为电视"))
                and existing["name"] not in renamed_defaults.get(item["id"], set())
            )
            name = existing["name"] if keep_existing_name else item["name"]
            migrated_zones = {
                "signage-15": {"电梯厅", "20F 电梯厅"},
                "signage-16": {"电梯厅", "20F 电梯厅"},
                "signage-14": {"产学研会议室"},
                "signage-28": {"未来场景研究前沿", "产学研会议室"},
                "signage-29": {"未来场景研究前沿", "产学研会议室"},
                "signage-30": {"未来场景研究前沿", "产学研会议室"},
            }
            migrate_zone = existing and existing["zone_id"] in migrated_zones.get(item["id"], set())
            zone = item["zone_id"] if migrate_zone else (
                existing["zone_id"] if existing and existing["zone_id"] and "展示区" not in existing["zone_id"] else item["zone_id"]
            )
            values = {
                **item, "name": name, "zone_id": zone, "status": "unverified", "current_scene": "ambient",
                "volume": 35, "last_seen": now_iso(), "simulated": 1, "power_mode": "active",
                "coord_x": existing["coord_x"] if existing else None, "coord_y": existing["coord_y"] if existing else None,
                "coord_z": existing["coord_z"] if existing else None, "coordinate_ref": "20F-plan-v1",
                "position_verified": existing["position_verified"] if existing else int(item["position_verified"]),
                "notes": existing["notes"] if existing else "", "input_source": "BaiPlayer", "updated_at": now_iso(),
                "mac_address": item.get("mac_address", ""), "network_mode": item.get("network_mode", ""),
                "subnet_mask": item.get("subnet_mask", ""), "gateway": item.get("gateway", ""),
                "dns_servers": item.get("dns_servers", []),
                "functional_group": item.get("functional_group", "未分组"),
            }
            db.execute(
                """INSERT INTO devices(id,name,type,zone_id,address,status,capabilities,current_scene,volume,last_seen,simulated,power_mode,
                category,model,os_name,power_supply,resolution,input_sources,control_status,audio_status,adapter,coord_x,coord_y,coord_z,
                coordinate_ref,position_verified,address_verified,notes,evidence,input_source,updated_at,
                mac_address,network_mode,subnet_mask,gateway,dns_servers,functional_group)
                VALUES(:id,:name,:type,:zone_id,:address,:status,:capabilities,:current_scene,:volume,:last_seen,:simulated,:power_mode,
                :category,:model,:os_name,:power_supply,:resolution,:input_sources,:control_status,:audio_status,:adapter,:coord_x,:coord_y,:coord_z,
                :coordinate_ref,:position_verified,:address_verified,:notes,:evidence,:input_source,:updated_at,
                :mac_address,:network_mode,:subnet_mask,:gateway,:dns_servers,:functional_group)
                ON CONFLICT(id) DO UPDATE SET name=excluded.name,type=excluded.type,zone_id=excluded.zone_id,address=excluded.address,
                category=excluded.category,model=excluded.model,os_name=excluded.os_name,power_supply=excluded.power_supply,
                resolution=excluded.resolution,input_sources=excluded.input_sources,control_status=excluded.control_status,
                audio_status=excluded.audio_status,adapter=excluded.adapter,address_verified=excluded.address_verified,
                evidence=excluded.evidence,updated_at=excluded.updated_at,mac_address=excluded.mac_address,
                network_mode=excluded.network_mode,subnet_mask=excluded.subnet_mask,gateway=excluded.gateway,
                dns_servers=excluded.dns_servers,functional_group=excluded.functional_group""",
                {**values, "capabilities": json.dumps(values["capabilities"], ensure_ascii=False),
                 "input_sources": json.dumps(values["input_sources"], ensure_ascii=False),
                 "control_status": json.dumps(values["control_status"], ensure_ascii=False),
                 "dns_servers": json.dumps(values["dns_servers"], ensure_ascii=False)},
            )

    def _ensure_initial_admin(self, db: sqlite3.Connection) -> None:
        credential_path = self.path.parent / "INITIAL_ADMIN_PASSWORD.txt"
        existing = db.execute("SELECT * FROM users ORDER BY created_at LIMIT 1").fetchone()
        if existing and (existing["username"] != "admin" or not existing["must_change_password"]):
            return
        if existing and credential_path.exists():
            lines = credential_path.read_text(encoding="utf-8").splitlines()
            saved = next((line.split("=", 1)[1] for line in lines if line.startswith("password=")), "")
            if saved and password_matches(saved, existing["password_hash"]):
                return
        password = os.getenv("BAIPLAYER_BOOTSTRAP_PASSWORD") or secrets.token_urlsafe(16)
        if existing:
            db.execute("UPDATE users SET password_hash=?,must_change_password=1 WHERE id=?", (password_hash(password), existing["id"]))
        else:
            db.execute(
                "INSERT INTO users VALUES(?,?,?,?,?,?,?,?)",
                ("user-admin", "admin", "系统管理员", password_hash(password), "admin", 1, 1, now_iso()),
            )
        credential_path.write_text(f"username=admin\npassword={password}\nchange_required=true\n", encoding="utf-8")
        os.chmod(credential_path, 0o600)

    def _seed(self, db: sqlite3.Connection) -> None:
        scenes = [
            ("ambient", "空间呼吸", "ambient", "#39c8ee", "让空间开始聆听", "BAIYIN · AOS", "", 0),
            ("welcome", "参观欢迎", "welcome", "#42d67c", "欢迎来到百音未来场景实验室", "空间正在回应你的到来", "", 18),
            ("product-a", "1号弧讲解", "product", "#78d9f1", "声音成为空间能力", "1号弧 · 自适应声场演示", "", 45),
            ("product-b", "2—4号弧讲解", "product", "#b69cff", "从设备到能力", "2—4号弧 · 一体化体验", "", 45),
            ("awareness", "空间意识", "awareness", "#f5bd27", "空间不再被设备驱动", "而是回应人的需要", "", 80),
            ("emergency", "紧急通知", "alert", "#ff5549", "请保持通道畅通", "系统已进入安全提示状态", "", 0),
        ]
        db.executemany("INSERT INTO scenes VALUES (?,?,?,?,?,?,?,?)", scenes)
        self.add_event("system", "runtime.seeded", payload={"version": VERSION}, db=db)

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        for key in ("capabilities", "payload", "input_sources", "control_status", "dns_servers", "targets", "snapshot"):
            if key in value:
                try:
                    value[key] = json.loads(value[key])
                except (TypeError, json.JSONDecodeError):
                    pass
        for key in ("simulated", "position_verified", "address_verified", "enabled", "must_change_password"):
            if key in value:
                value[key] = bool(value[key])
        return value

    def devices(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            return [self._row(row) for row in db.execute("SELECT * FROM devices ORDER BY zone_id,name")]

    def scenes(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            return [self._row(row) for row in db.execute("SELECT * FROM scenes ORDER BY rowid")]

    def deployments(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM deployments ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (max(1, min(limit, 100)),),
            )
            return [self._row(row) for row in rows]

    def prepare_deployment(self, scene_id: str, targets: list[str], target_group: str = "") -> dict[str, Any]:
        unique_targets = list(dict.fromkeys(targets))
        devices = {item["id"]: item for item in self.devices()}
        if scene_id not in {scene["id"] for scene in self.scenes()}:
            raise ValueError("valid sceneId is required")
        if not unique_targets:
            raise ValueError("at least one target device is required")
        unknown = [item for item in unique_targets if item not in devices]
        if unknown:
            raise ValueError(f"unknown device targets: {', '.join(unknown)}")
        snapshot = [{
            "deviceId": device_id,
            "currentScene": devices[device_id]["current_scene"],
            "powerMode": devices[device_id]["power_mode"],
            "volume": devices[device_id]["volume"],
            "inputSource": devices[device_id]["input_source"],
        } for device_id in unique_targets]
        item = {
            "id": f"dep-{uuid.uuid4().hex[:12]}", "created_at": now_iso(), "scene_id": scene_id,
            "target_group": target_group[:120], "targets": unique_targets, "snapshot": snapshot,
            "command_id": "", "status": "prepared", "restored_at": "",
        }
        with self._connect() as db:
            db.execute(
                "INSERT INTO deployments VALUES(?,?,?,?,?,?,?,?,?)",
                (item["id"], item["created_at"], scene_id, item["target_group"],
                 json.dumps(unique_targets, ensure_ascii=False), json.dumps(snapshot, ensure_ascii=False), "", "prepared", ""),
            )
        return item

    def finish_deployment(self, deployment_id: str, command_id: str, status: str) -> dict[str, Any]:
        with self._connect() as db:
            result = db.execute(
                "UPDATE deployments SET command_id=?,status=? WHERE id=?",
                (command_id, status, deployment_id),
            )
            if result.rowcount != 1:
                raise ValueError("deployment not found")
            row = db.execute("SELECT * FROM deployments WHERE id=?", (deployment_id,)).fetchone()
        return self._row(row)

    def restore_deployment(self, deployment_id: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM deployments WHERE id=?", (deployment_id,)).fetchone()
            if not row:
                raise ValueError("deployment not found")
            deployment = self._row(row)
            if deployment["restored_at"]:
                raise ValueError("deployment has already been restored")
            snapshot = deployment["snapshot"]
            for state in snapshot:
                db.execute(
                    "UPDATE devices SET current_scene=?,power_mode=?,volume=?,input_source=?,updated_at=? WHERE id=?",
                    (state["currentScene"], state["powerMode"], state["volume"], state["inputSource"], now_iso(), state["deviceId"]),
                )
            command = {
                "id": f"cmd-{uuid.uuid4().hex[:12]}", "created_at": now_iso(), "command_type": "restore_state",
                "targets": deployment["targets"], "zone_id": "", "scene_id": "", "state_map": snapshot,
                "priority": 90, "source": "operator", "status": "dispatched",
            }
            db.execute(
                "INSERT INTO commands VALUES(?,?,?,?,?,?,?)",
                (command["id"], command["created_at"], "restore_state",
                 json.dumps(command["targets"], ensure_ascii=False), "", json.dumps(command, ensure_ascii=False), "dispatched"),
            )
            restored_at = now_iso()
            db.execute(
                "UPDATE deployments SET status='restored',restored_at=? WHERE id=?",
                (restored_at, deployment_id),
            )
            devices = [self._row(device) for device in db.execute(
                f"SELECT * FROM devices WHERE id IN ({','.join('?' for _ in command['targets'])})",
                command["targets"],
            )]
        deployment.update(status="restored", restored_at=restored_at)
        return {"deployment": deployment, "command": command, "devices": devices}

    def authenticate(self, username: str, password: str) -> tuple[str, dict[str, Any]]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM users WHERE username=? AND enabled=1", (username.strip().lower(),)).fetchone()
            if not row or not password_matches(password, row["password_hash"]):
                raise PermissionError("用户名或密码错误")
            token = secrets.token_urlsafe(32)
            timestamp = int(time.time())
            db.execute("DELETE FROM sessions WHERE expires_at < ?", (timestamp,))
            db.execute("INSERT INTO sessions VALUES(?,?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), row["id"], timestamp, timestamp + SESSION_TTL_SECONDS))
            user = self._row(row)
            user.pop("password_hash", None)
            return token, user

    def rotate_initial_admin(self) -> Path:
        """Replace the bootstrap admin credential and invalidate its sessions."""
        password = secrets.token_urlsafe(16)
        credential_path = self.path.parent / "INITIAL_ADMIN_PASSWORD.txt"
        with self._lock, self._connect() as db:
            row = db.execute("SELECT id FROM users WHERE username='admin'").fetchone()
            if not row:
                raise KeyError("admin")
            db.execute(
                "UPDATE users SET password_hash=?,must_change_password=1 WHERE id=?",
                (password_hash(password), row["id"]),
            )
            db.execute("DELETE FROM sessions WHERE user_id=?", (row["id"],))
        credential_path.write_text(
            f"username=admin\npassword={password}\nchange_required=true\n",
            encoding="utf-8",
        )
        os.chmod(credential_path, 0o600)
        return credential_path

    def session_user(self, token: str) -> dict[str, Any] | None:
        if not token:
            return None
        with self._connect() as db:
            row = db.execute(
                """SELECT users.* FROM sessions JOIN users ON users.id=sessions.user_id
                WHERE sessions.token_hash=? AND sessions.expires_at>=? AND users.enabled=1""",
                (hashlib.sha256(token.encode()).hexdigest(), int(time.time())),
            ).fetchone()
        if not row:
            return None
        user = self._row(row)
        user.pop("password_hash", None)
        return user

    def logout(self, token: str) -> None:
        if not token:
            return
        with self._connect() as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))

    def users(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT id,username,display_name,role,enabled,must_change_password,created_at FROM users ORDER BY username")
            return [self._row(row) for row in rows]

    def create_user(self, payload: dict[str, Any]) -> dict[str, Any]:
        username = str(payload.get("username", "")).strip().lower()
        display_name = str(payload.get("displayName", "")).strip()
        role = str(payload.get("role", "viewer"))
        password = str(payload.get("password", ""))
        if not re.fullmatch(r"[a-z0-9._-]{3,40}", username) or not display_name:
            raise ValueError("username or displayName is invalid")
        if role not in ROLE_LEVEL:
            raise ValueError("invalid role")
        item = {"id": f"user-{uuid.uuid4().hex[:12]}", "username": username, "display_name": display_name[:80],
                "role": role, "enabled": True, "must_change_password": True, "created_at": now_iso()}
        with self._connect() as db:
            try:
                db.execute("INSERT INTO users VALUES(?,?,?,?,?,?,?,?)", (item["id"], username, item["display_name"], password_hash(password), role, 1, 1, item["created_at"]))
            except sqlite3.IntegrityError as exc:
                raise ValueError("username already exists") from exc
        return item

    def update_user(self, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if not row:
                raise ValueError("user not found")
            role = str(payload.get("role", row["role"]))
            if role not in ROLE_LEVEL:
                raise ValueError("invalid role")
            enabled = int(bool(payload.get("enabled", row["enabled"])))
            password = str(payload.get("password", ""))
            if password:
                db.execute("UPDATE users SET role=?,enabled=?,password_hash=?,must_change_password=1 WHERE id=?", (role, enabled, password_hash(password), user_id))
            else:
                db.execute("UPDATE users SET role=?,enabled=? WHERE id=?", (role, enabled, user_id))
            updated = db.execute("SELECT id,username,display_name,role,enabled,must_change_password,created_at FROM users WHERE id=?", (user_id,)).fetchone()
        return self._row(updated)

    def change_password(self, user_id: str, current_password: str, new_password: str) -> None:
        with self._connect() as db:
            row = db.execute("SELECT password_hash FROM users WHERE id=?", (user_id,)).fetchone()
            if not row or not password_matches(current_password, row["password_hash"]):
                raise PermissionError("当前密码错误")
            db.execute("UPDATE users SET password_hash=?,must_change_password=0 WHERE id=?", (password_hash(new_password), user_id))

    def create_scene(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = str(payload.get("name", "")).strip()
        title = str(payload.get("title", "")).strip()
        if not name or not title:
            raise ValueError("name and title are required")
        requested_id = str(payload.get("id", "")).strip().lower()
        scene_id = re.sub(r"[^a-z0-9-]+", "-", requested_id).strip("-") or f"scene-{uuid.uuid4().hex[:8]}"
        media_url = str(payload.get("mediaUrl", ""))[:500]
        if media_url and not (media_url.startswith("/media/") or media_url.startswith("https://")):
            raise ValueError("mediaUrl must use /media/ or https://")
        scene = {
            "id": scene_id, "name": name[:80], "kind": str(payload.get("kind", "information"))[:30],
            "accent": str(payload.get("accent", "#39c8ee"))[:20], "title": title[:160],
            "subtitle": str(payload.get("subtitle", ""))[:200], "media_url": media_url,
            "duration": max(0, min(86400, int(payload.get("duration", 0)))),
        }
        with self._connect() as db:
            db.execute("INSERT INTO scenes VALUES (?,?,?,?,?,?,?,?)", tuple(scene.values()))
        return scene

    def update_scene(self, scene_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        scene_id = str(scene_id).strip()
        name = str(payload.get("name", "")).strip()
        title = str(payload.get("title", "")).strip()
        if not scene_id or not name or not title:
            raise ValueError("scene id, name and title are required")
        media_url = str(payload.get("mediaUrl", ""))[:500]
        if media_url and not (media_url.startswith("/media/") or media_url.startswith("https://")):
            raise ValueError("mediaUrl must use /media/ or https://")
        scene = {
            "id": scene_id, "name": name[:80], "kind": str(payload.get("kind", "information"))[:30],
            "accent": str(payload.get("accent", "#39c8ee"))[:20], "title": title[:160],
            "subtitle": str(payload.get("subtitle", ""))[:200], "media_url": media_url,
            "duration": max(0, min(86400, int(payload.get("duration", 0)))),
        }
        with self._connect() as db:
            result = db.execute(
                "UPDATE scenes SET name=?,kind=?,accent=?,title=?,subtitle=?,media_url=?,duration=? WHERE id=?",
                (scene["name"], scene["kind"], scene["accent"], scene["title"], scene["subtitle"],
                 scene["media_url"], scene["duration"], scene_id),
            )
            if result.rowcount != 1:
                raise ValueError("scene not found")
        return scene

    def update_device(self, device_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        name = str(payload.get("name", "")).strip()
        location_name = str(payload.get("locationName", "")).strip()
        address = str(payload.get("address", "")).strip()
        if not device_id or not name or not location_name:
            raise ValueError("device id, name and locationName are required")
        if address and not re.fullmatch(r"(?:\d{1,3}\.){3}\d{1,3}", address):
            raise ValueError("address must be an IPv4 address or empty")
        coords = []
        for key in ("coordX", "coordY", "coordZ"):
            value = payload.get(key)
            coords.append(None if value in (None, "") else round(float(value), 3))
        audio_status = str(payload.get("audioStatus", "pending_verification"))
        if audio_status not in {"available", "available_after_adapter", "pending_verification", "unavailable"}:
            raise ValueError("invalid audioStatus")
        with self._connect() as db:
            result = db.execute(
                """UPDATE devices SET name=?,zone_id=?,address=?,coord_x=?,coord_y=?,coord_z=?,coordinate_ref=?,
                position_verified=?,address_verified=?,audio_status=?,notes=?,updated_at=? WHERE id=?""",
                (name[:100], location_name[:100], address, *coords, str(payload.get("coordinateRef", "20F-plan-v1"))[:80],
                 int(bool(payload.get("positionVerified"))), int(bool(payload.get("addressVerified"))), audio_status,
                 str(payload.get("notes", ""))[:500], now_iso(), device_id),
            )
            if result.rowcount != 1:
                raise ValueError("device not found")
            row = db.execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone()
        return self._row(row)

    def update_device_location(self, device_id: str, location_name: str) -> dict[str, Any]:
        device = next((item for item in self.devices() if item["id"] == device_id), None)
        if not device:
            raise ValueError("device not found")
        return self.update_device(device_id, {
            "name": device["name"], "locationName": location_name, "address": device["address"],
            "coordX": device["coord_x"], "coordY": device["coord_y"], "coordZ": device["coord_z"],
            "coordinateRef": device["coordinate_ref"], "positionVerified": device["position_verified"],
            "addressVerified": device["address_verified"], "audioStatus": device["audio_status"], "notes": device["notes"],
        })

    def events(self, limit: int = 40) -> list[dict[str, Any]]:
        with self._connect() as db:
            return [self._row(row) for row in db.execute(
                "SELECT * FROM events ORDER BY occurred_at DESC, rowid DESC LIMIT ?", (max(1, min(limit, 200)),)
            )]

    def add_event(
        self, source: str, event_type: str, zone_id: str = "", subject_id: str = "",
        confidence: float = 1.0, payload: dict[str, Any] | None = None,
        result: str = "accepted", db: sqlite3.Connection | None = None,
    ) -> dict[str, Any]:
        item = {
            "id": f"evt-{uuid.uuid4().hex[:12]}", "occurred_at": now_iso(), "source": source,
            "event_type": event_type, "zone_id": zone_id, "subject_id": subject_id,
            "confidence": float(confidence), "payload": payload or {}, "result": result,
        }
        values = (*[item[key] for key in ("id", "occurred_at", "source", "event_type", "zone_id", "subject_id", "confidence")], json.dumps(item["payload"], ensure_ascii=False), result)
        if db is not None:
            db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?)", values)
        else:
            with self._connect() as connection:
                connection.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?)", values)
        return item

    def register_device(self, payload: dict[str, Any]) -> dict[str, Any]:
        device_id = str(payload.get("deviceId", "")).strip()
        if not device_id:
            raise ValueError("deviceId is required")
        timestamp = now_iso()
        with self._connect() as db:
            db.execute(
                """INSERT INTO devices(id,name,type,zone_id,address,status,capabilities,current_scene,volume,last_seen,simulated)
                VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,type=excluded.type,zone_id=excluded.zone_id,address=excluded.address,
                status='online',capabilities=excluded.capabilities,last_seen=excluded.last_seen""",
                (device_id, payload.get("name", device_id), payload.get("type", "web"),
                 payload.get("zoneId", "未分区"), payload.get("address", ""), "online",
                 json.dumps(payload.get("capabilities", ["image", "video", "audio", "web"])),
                 "ambient", int(payload.get("volume", 35)), timestamp, 1),
            )
            row = db.execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone()
        return self._row(row)

    def heartbeat(self, device_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self._connect() as db:
            db.execute(
                "UPDATE devices SET status='online',last_seen=?,current_scene=COALESCE(NULLIF(?,''),current_scene) WHERE id=?",
                (now_iso(), payload.get("sceneId", ""), device_id),
            )
            row = db.execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone()
        if not row:
            raise KeyError(device_id)
        return self._row(row)

    def create_command(self, payload: dict[str, Any]) -> dict[str, Any]:
        command_type = str(payload.get("type", "play_scene"))
        if command_type not in ALLOWED_COMMAND_TYPES:
            raise ValueError("unsupported command type")
        raw_targets = payload.get("targets") or ([payload["deviceId"]] if payload.get("deviceId") else [])
        if not isinstance(raw_targets, list) or any(not isinstance(item, str) for item in raw_targets):
            raise ValueError("targets must be a list of device ids")
        targets = list(dict.fromkeys(raw_targets))
        zone_id = str(payload.get("zoneId", ""))
        if not targets and not zone_id:
            raise ValueError("targets or zoneId is required")
        devices = self.devices()
        known_ids = {device["id"] for device in devices}
        unknown_targets = [target for target in targets if target not in known_ids]
        if unknown_targets:
            raise ValueError(f"unknown device targets: {', '.join(unknown_targets)}")
        if zone_id and zone_id not in {device["zone_id"] for device in devices}:
            raise ValueError("unknown zoneId")
        if command_type == "play_scene":
            scene_id = str(payload.get("sceneId", ""))
            if not scene_id or scene_id not in {scene["id"] for scene in self.scenes()}:
                raise ValueError("valid sceneId is required")
        command = {
            "id": f"cmd-{uuid.uuid4().hex[:12]}", "created_at": now_iso(),
            "command_type": command_type, "targets": targets, "zone_id": zone_id,
            "scene_id": str(payload.get("sceneId", "")), "volume": payload.get("volume"),
            "power_mode": str(payload.get("powerMode", "")),
            "input_source": str(payload.get("inputSource", ""))[:80],
            "priority": int(payload.get("priority", 50)), "source": payload.get("source", "operator"),
            "status": "dispatched",
        }
        with self._connect() as db:
            db.execute("INSERT INTO commands VALUES (?,?,?,?,?,?,?)", (
                command["id"], command["created_at"], command_type,
                json.dumps(targets or {"zoneId": zone_id}, ensure_ascii=False), command["scene_id"],
                json.dumps(command, ensure_ascii=False), command["status"],
            ))
            if command_type == "play_scene" and command["scene_id"]:
                if targets:
                    placeholders = ",".join("?" for _ in targets)
                    db.execute(f"UPDATE devices SET current_scene=? WHERE id IN ({placeholders})", [command["scene_id"], *targets])
                else:
                    db.execute("UPDATE devices SET current_scene=? WHERE zone_id=?", (command["scene_id"], zone_id))
            if command_type == "set_volume" and command["volume"] is not None:
                volume = max(0, min(100, int(command["volume"])))
                if targets:
                    placeholders = ",".join("?" for _ in targets)
                    db.execute(f"UPDATE devices SET volume=? WHERE id IN ({placeholders})", [volume, *targets])
                else:
                    db.execute("UPDATE devices SET volume=? WHERE zone_id=?", (volume, zone_id))
            if command_type == "set_power_mode":
                power_mode = command["power_mode"].lower()
                if power_mode not in {"active", "eco", "standby"}:
                    raise ValueError("powerMode must be active, eco or standby")
                if targets:
                    placeholders = ",".join("?" for _ in targets)
                    db.execute(f"UPDATE devices SET power_mode=? WHERE id IN ({placeholders})", [power_mode, *targets])
                else:
                    db.execute("UPDATE devices SET power_mode=? WHERE zone_id=?", (power_mode, zone_id))
            if command_type == "set_input_source":
                if not command["input_source"]:
                    raise ValueError("inputSource is required")
                target_devices = [item for item in devices if item["id"] in targets] if targets else [item for item in devices if item["zone_id"] == zone_id]
                if any(command["input_source"] not in item.get("input_sources", []) for item in target_devices):
                    raise ValueError("inputSource is not declared by all target devices")
                if targets:
                    placeholders = ",".join("?" for _ in targets)
                    db.execute(f"UPDATE devices SET input_source=? WHERE id IN ({placeholders})", [command["input_source"], *targets])
                else:
                    db.execute("UPDATE devices SET input_source=? WHERE zone_id=?", (command["input_source"], zone_id))
        return command

    def acknowledge_command(self, command_id: str, device_id: str, status: str) -> dict[str, Any]:
        allowed = {"received", "playing", "completed", "failed"}
        if status not in allowed:
            raise ValueError(f"status must be one of: {', '.join(sorted(allowed))}")
        acknowledged_at = now_iso()
        with self._connect() as db:
            command_row = db.execute("SELECT payload FROM commands WHERE id=?", (command_id,)).fetchone()
            if not command_row:
                raise KeyError(command_id)
            command = json.loads(command_row["payload"])
            expected_targets = command.get("targets", [])
            if expected_targets and device_id not in expected_targets:
                raise ValueError("device is not a target of this command")
            db.execute(
                "INSERT INTO command_acks(command_id,device_id,status,acknowledged_at) VALUES(?,?,?,?) "
                "ON CONFLICT(command_id,device_id) DO UPDATE SET status=excluded.status,acknowledged_at=excluded.acknowledged_at",
                (command_id, device_id, status, acknowledged_at),
            )
            statuses = [row[0] for row in db.execute("SELECT status FROM command_acks WHERE command_id=?", (command_id,))]
            if "failed" in statuses:
                aggregate = "failed"
            elif expected_targets and len(statuses) >= len(expected_targets) and all(item == "completed" for item in statuses):
                aggregate = "completed"
            elif "playing" in statuses:
                aggregate = "playing"
            else:
                aggregate = "received"
            db.execute("UPDATE commands SET status=? WHERE id=?", (aggregate, command_id))
        return {"commandId": command_id, "deviceId": device_id, "status": status, "commandStatus": aggregate, "acknowledgedAt": acknowledged_at}


class AOSReadOnlyProbe(threading.Thread):
    """Polls only safe GET endpoints. Never sends a device command."""

    def __init__(self, base_url: str) -> None:
        super().__init__(name="aos-readonly-probe", daemon=True)
        self.base_url = base_url.rstrip("/")
        self.state: dict[str, Any] = {
            "configured": bool(base_url), "online": False, "mode": "GET_ONLY",
            "lastCheck": None, "error": "未配置AOS母机地址" if not base_url else "等待首次查询",
            "runtime": None,
        }
        self._stop_event = threading.Event()

    def run(self) -> None:
        while not self._stop_event.is_set():
            self.check()
            self._stop_event.wait(8)

    def check(self) -> dict[str, Any]:
        if not self.base_url:
            return self.state
        errors = []
        for path in ("/api/show/runtime", "/api/runtime/health"):
            url = f"{self.base_url}{path}"
            try:
                request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": f"BaiPlayer/{VERSION}"})
                with urllib.request.urlopen(request, timeout=2.5) as response:
                    data = json.loads(response.read(MAX_BODY_BYTES))
                runtime = data.get("runtime", data)
                hardware = data.get("hardware", {})
                self.state.update(
                    online=True, error="", lastCheck=now_iso(), endpoint=path,
                    runtime={
                        "version": runtime.get("version"), "status": runtime.get("status"),
                        "mode": runtime.get("mode"), "sceneIndex": runtime.get("sceneIndex"),
                        "audioMuted": runtime.get("audioMuted"), "updatedAt": runtime.get("updatedAt"),
                        "fieldOutputEnabled": hardware.get("fieldOutputEnabled"),
                        "hardwareBusy": hardware.get("busy"),
                    },
                )
                break
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
                errors.append(f"{path}: {exc}")
        else:
            self.state.update(online=False, error=" | ".join(errors), lastCheck=now_iso(), runtime=None)
        return self.state


class BaiPlayerApp:
    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config
        self.store = Store(DB_PATH)
        self.broker = EventBroker()
        self._field_last_dispatch: dict[tuple[str, str], float] = {}
        self.started_at = time.monotonic()
        self.aos_probe = AOSReadOnlyProbe(config.aos_base_url)
        self.aos_probe.start()
        self.huawei = HuaweiDLNAAdapter(subnet=os.getenv("BAIPLAYER_DEVICE_SUBNET", os.getenv("BAIPLAYER_TV_SUBNET", "192.0.2.0/24")))

    def _real_targets(self, command: dict[str, Any]) -> list[dict[str, Any]]:
        devices = self.store.devices()
        if command["targets"]:
            wanted = set(command["targets"])
            return [device for device in devices if device["id"] in wanted]
        return [device for device in devices if device["zone_id"] == command["zone_id"]]

    def _execute_real_command(self, command: dict[str, Any]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        scenes = {scene["id"]: scene for scene in self.store.scenes()}
        for device in self._real_targets(command):
            item: dict[str, Any] = {"deviceId": device["id"], "address": device["address"], "adapter": device["adapter"]}
            if device["adapter"] != "harmonyos_adapter":
                item.update(ok=False, status="adapter_not_implemented")
                results.append(item)
                continue
            try:
                if command["command_type"] == "play_scene":
                    scene = scenes[command["scene_id"]]
                    if not scene["media_url"]:
                        raise ValueError("scene has no deployable mediaUrl")
                    media_url = scene["media_url"]
                    if media_url.startswith("/"):
                        if not self.config.public_base_url:
                            raise ValueError("BAIPLAYER_PUBLIC_BASE_URL is not configured")
                        media_url = self.config.public_base_url.rstrip("/") + media_url
                    item.update(self.huawei.push(device["address"], media_url, scene["name"]))
                    item.update(mediaUrl=media_url, status="pushed")
                elif command["command_type"] == "set_volume":
                    item.update(self.huawei.set_volume(device["address"], command["volume"]))
                    item["status"] = "applied"
                else:
                    item.update(ok=False, status="command_not_supported_by_dlna")
            except Exception as exc:
                item.update(ok=False, status="failed", error=str(exc))
            results.append(item)
        return results

    def health(self) -> dict[str, Any]:
        devices = self.store.devices()
        return {
            "ok": True, "service": "BaiPlayer", "version": VERSION, "mode": self.config.mode,
            "realDeviceOutput": self.config.real_device_output,
            "uptimeSeconds": round(time.monotonic() - self.started_at),
            "deviceCount": len(devices), "onlineCount": sum(d["status"] == "online" for d in devices),
            "aos": self.aos_probe.state, "timestamp": now_iso(),
        }

    def bootstrap(self) -> dict[str, Any]:
        devices = self.store.devices()
        zones = sorted({device["zone_id"] for device in devices})
        category_counts = {category: sum(item["category"] == category for item in devices) for category in ("signage", "tv", "audio", "ops", "led")}
        return {
            "version": VERSION, "mode": self.config.mode, "realDeviceOutput": self.config.real_device_output,
            "devices": devices, "scenes": self.store.scenes(), "events": self.store.events(),
            "deployments": self.store.deployments(),
            "zones": zones, "categoryCounts": category_counts, "aos": self.aos_probe.state,
            "safety": ["仅管理信息与媒体推送", "仅管理显示终端节能与待机", "不包含灯光控制", "不包含电机及其他机构控制"],
        }

    def dispatch(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.config.allow_simulation_writes:
            raise PermissionError("simulation writes disabled")
        command = self.store.create_command(payload)
        if self.config.real_device_output:
            command["realResults"] = self._execute_real_command(command)
            command["status"] = "completed" if command["realResults"] and all(item.get("ok") for item in command["realResults"]) else "partial"
        channels = list(command["targets"])
        if not channels and command["zone_id"]:
            channels = [device["id"] for device in self.store.devices() if device["zone_id"] == command["zone_id"]]
            channels.append(f"zone:{command['zone_id']}")
        for channel in channels:
            self.broker.publish(channel, {"event": "command", "data": command})
        self.broker.publish("dashboard", {"event": "command", "data": command})
        event = self.store.add_event(
            command["source"], f"player.{command['command_type']}", command["zone_id"],
            payload={"commandId": command["id"], "targets": command["targets"], "sceneId": command["scene_id"]},
            result="simulated" if not self.config.real_device_output else command["status"],
        )
        self.broker.publish("dashboard", {"event": "field-event", "data": event})
        return command

    def deploy(self, payload: dict[str, Any]) -> dict[str, Any]:
        scene_id = str(payload.get("sceneId", ""))
        raw_targets = payload.get("targets", [])
        if not isinstance(raw_targets, list) or any(not isinstance(item, str) for item in raw_targets):
            raise ValueError("targets must be a list of device ids")
        deployment = self.store.prepare_deployment(scene_id, raw_targets, str(payload.get("targetGroup", "")))
        try:
            command = self.dispatch({
                "type": "play_scene", "targets": deployment["targets"], "sceneId": scene_id,
                "source": "operator", "priority": 70,
            })
        except Exception:
            self.store.finish_deployment(deployment["id"], "", "failed")
            raise
        status = "simulated" if not self.config.real_device_output else command["status"]
        deployment = self.store.finish_deployment(deployment["id"], command["id"], status)
        event = self.store.add_event(
            "operator", "content.deployed", subject_id=deployment["id"],
            payload={"sceneId": scene_id, "targets": deployment["targets"], "targetGroup": deployment["target_group"]},
            result=status,
        )
        self.broker.publish("dashboard", {"event": "field-event", "data": event})
        return {"deployment": deployment, "command": command}

    def restore(self, deployment_id: str) -> dict[str, Any]:
        result = self.store.restore_deployment(deployment_id)
        command = result["command"]
        for channel in command["targets"]:
            self.broker.publish(channel, {"event": "command", "data": command})
        self.broker.publish("dashboard", {"event": "command", "data": command})
        event = self.store.add_event(
            "operator", "content.deployment_restored", subject_id=deployment_id,
            payload={"commandId": command["id"], "targets": command["targets"]},
            result="simulated" if not self.config.real_device_output else "field-gated",
        )
        self.broker.publish("dashboard", {"event": "field-event", "data": event})
        return result

    def accept_field_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        event_type = str(payload.get("eventType", "")).strip()
        if not event_type:
            raise ValueError("eventType is required")
        confidence = float(payload.get("confidence", 1))
        if not 0 <= confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        sensor_payload = payload.get("payload", {})
        if not isinstance(sensor_payload, dict):
            raise ValueError("payload must be an object")
        if RAW_SENSOR_KEYS.intersection(sensor_payload):
            raise ValueError("raw audio, video and image data are not accepted")
        rules = {
            "visitor.entered_zone": "welcome", "visitor.dwell_arc_1": "product-a",
            "visitor.dwell_arc_2_4": "product-b", "visitor.dwell_product_a": "product-a",
            "visitor.dwell_product_b": "product-b", "system.awareness": "awareness",
        }
        zone_id = str(payload.get("zoneId", ""))
        scene_id = rules.get(event_type)
        decision = "accepted"
        now = time.monotonic()
        cooldown_key = (event_type, zone_id)
        if scene_id and confidence < FIELD_TRIGGER_MIN_CONFIDENCE:
            decision = "ignored_low_confidence"
        elif scene_id and now - self._field_last_dispatch.get(cooldown_key, -FIELD_TRIGGER_COOLDOWN_SECONDS) < FIELD_TRIGGER_COOLDOWN_SECONDS:
            decision = "ignored_cooldown"
        event = self.store.add_event(
            str(payload.get("source", "BaiField")), event_type, zone_id,
            str(payload.get("subjectId", "")), confidence, sensor_payload, result=decision,
        )
        self.broker.publish("dashboard", {"event": "field-event", "data": event})
        command = None
        if scene_id and event["zone_id"] and decision == "accepted":
            self._field_last_dispatch[cooldown_key] = now
            command = self.dispatch({"type": "play_scene", "zoneId": event["zone_id"], "sceneId": scene_id, "source": "BaiField", "priority": 60})
        return {"event": event, "command": command, "decision": decision}


APP: BaiPlayerApp


class Handler(SimpleHTTPRequestHandler):
    server_version = f"BaiPlayer/{VERSION}"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[{now_iso()}] {self.client_address[0]} {fmt % args}")

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data: https:; media-src 'self' https:; "
            "style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'",
        )
        if self.path.startswith("/api/"):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _json(self, payload: Any, status: int = 200, headers: dict[str, str] | None = None) -> None:
        body = json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, message: str) -> None:
        self._json({"ok": False, "error": message}, status)

    def _body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("invalid content length") from exc
        if length <= 0 or length > MAX_BODY_BYTES:
            raise ValueError("request body is empty or too large")
        try:
            value = json.loads(self.rfile.read(length))
        except json.JSONDecodeError as exc:
            raise ValueError("invalid JSON") from exc
        if not isinstance(value, dict):
            raise ValueError("JSON object required")
        return value

    def _authorized(self) -> bool:
        expected = APP.config.api_token
        return not expected or self.headers.get("X-AOS-Token", "") == expected

    def _session_token(self) -> str:
        for part in self.headers.get("Cookie", "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == "baiplayer_session":
                return value
        return ""

    def _user(self) -> dict[str, Any] | None:
        return APP.store.session_user(self._session_token())

    def _require_role(self, role: str = "viewer") -> dict[str, Any] | None:
        user = self._user()
        if not user:
            self._error(HTTPStatus.UNAUTHORIZED, "login required")
            return None
        if ROLE_LEVEL.get(user["role"], -1) < ROLE_LEVEL[role]:
            self._error(HTTPStatus.FORBIDDEN, "insufficient role")
            return None
        return user

    def do_GET(self) -> None:  # noqa: N802
        route = urlparse(self.path)
        if route.path == "/api/v1/health":
            return self._json(APP.health())
        if route.path == "/api/v1/auth/status":
            user = self._user()
            return self._json({"authenticated": bool(user), "user": user})
        if route.path.startswith("/api/v1/") and not self._require_role("viewer"):
            return
        if route.path == "/api/v1/bootstrap":
            return self._json(APP.bootstrap())
        if route.path == "/api/v1/devices":
            return self._json({"items": APP.store.devices()})
        if route.path == "/api/v1/users":
            if not self._require_role("admin"):
                return
            return self._json({"items": APP.store.users()})
        if route.path == "/api/v1/baifield/devices":
            items = [{key: device.get(key) for key in (
                "id", "name", "category", "zone_id", "address", "coord_x", "coord_y", "coord_z",
                "coordinate_ref", "position_verified", "audio_status", "adapter", "control_status", "functional_group",
            )} for device in APP.store.devices()]
            return self._json({"coordinateSystem": "20F-plan-v1", "units": "metre", "items": items})
        if route.path == "/api/v1/scenes":
            return self._json({"items": APP.store.scenes()})
        if route.path == "/api/v1/deployments":
            return self._json({"items": APP.store.deployments()})
        if route.path == "/api/v1/events":
            limit = int(parse_qs(route.query).get("limit", ["40"])[0])
            return self._json({"items": APP.store.events(limit)})
        if route.path == "/api/v1/aos/status":
            return self._json(APP.aos_probe.check())
        if route.path == "/api/v1/stream":
            return self._stream(parse_qs(route.query))
        if route.path == "/":
            self.path = "/index.html"
        return super().do_GET()

    def _stream(self, query: dict[str, list[str]]) -> None:
        channel = query.get("deviceId", query.get("channel", ["dashboard"]))[0]
        target = APP.broker.subscribe(channel)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            self.wfile.write(b"event: ready\ndata: {\"connected\":true}\n\n")
            self.wfile.flush()
            while True:
                try:
                    item = target.get(timeout=15)
                    packet = f"event: {item['event']}\ndata: {json.dumps(item['data'], ensure_ascii=False)}\n\n".encode()
                except queue.Empty:
                    packet = b": heartbeat\n\n"
                self.wfile.write(packet)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            APP.broker.unsubscribe(channel, target)

    def do_POST(self) -> None:  # noqa: N802
        route = urlparse(self.path).path
        try:
            payload = self._body()
            if route == "/api/v1/auth/login":
                token, user = APP.store.authenticate(str(payload.get("username", "")), str(payload.get("password", "")))
                cookie = f"baiplayer_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={SESSION_TTL_SECONDS}"
                return self._json({"ok": True, "user": user}, headers={"Set-Cookie": cookie})
            if route == "/api/v1/auth/logout":
                APP.store.logout(self._session_token())
                return self._json({"ok": True}, headers={"Set-Cookie": "baiplayer_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"})
            if route == "/api/v1/auth/password":
                user = self._require_role("viewer")
                if not user:
                    return
                APP.store.change_password(user["id"], str(payload.get("currentPassword", "")), str(payload.get("newPassword", "")))
                return self._json({"ok": True})
            if route == "/api/v1/users":
                if not self._require_role("admin"):
                    return
                item = APP.store.create_user(payload)
                return self._json({"ok": True, "user": item}, HTTPStatus.CREATED)
            if route == "/api/v1/devices/register":
                if not self._authorized():
                    return self._error(HTTPStatus.UNAUTHORIZED, "invalid X-AOS-Token")
                device = APP.store.register_device(payload)
                return self._json({"ok": True, "device": device}, HTTPStatus.CREATED)
            if route == "/api/v1/scenes":
                scene = APP.store.create_scene(payload)
                event = APP.store.add_event("operator", "content.scene_created", payload={"sceneId": scene["id"]})
                APP.broker.publish("dashboard", {"event": "field-event", "data": event})
                return self._json({"ok": True, "scene": scene}, HTTPStatus.CREATED)
            if route == "/api/v1/devices/heartbeat":
                if not self._authorized():
                    return self._error(HTTPStatus.UNAUTHORIZED, "invalid X-AOS-Token")
                device = APP.store.heartbeat(str(payload.get("deviceId", "")), payload)
                return self._json({"ok": True, "device": device})
            if not self._require_role("operator"):
                return
            if route == "/api/v1/commands":
                command = APP.dispatch(payload)
                return self._json({"ok": True, "command": command}, HTTPStatus.ACCEPTED)
            if route == "/api/v1/deployments":
                result = APP.deploy(payload)
                return self._json({"ok": True, **result}, HTTPStatus.ACCEPTED)
            restore_match = re.fullmatch(r"/api/v1/deployments/(dep-[a-f0-9]+)/restore", route)
            if restore_match:
                result = APP.restore(restore_match.group(1))
                return self._json({"ok": True, **result}, HTTPStatus.ACCEPTED)
            if route == "/api/v1/commands/ack":
                ack = APP.store.acknowledge_command(
                    str(payload.get("commandId", "")), str(payload.get("deviceId", "")),
                    str(payload.get("status", "received")),
                )
                event = APP.store.add_event(
                    "BaiPlayer", "player.command_ack", subject_id=ack["deviceId"], payload=ack,
                    result=ack["status"],
                )
                APP.broker.publish("dashboard", {"event": "field-event", "data": event})
                return self._json({"ok": True, "ack": ack})
            if route == "/api/v1/field/events":
                result = APP.accept_field_event(payload)
                return self._json({"ok": True, **result}, HTTPStatus.ACCEPTED)
            return self._error(HTTPStatus.NOT_FOUND, "unknown API endpoint")
        except PermissionError as exc:
            status = HTTPStatus.UNAUTHORIZED if route.startswith("/api/v1/auth/") else HTTPStatus.LOCKED
            return self._error(status, str(exc))
        except KeyError as exc:
            return self._error(HTTPStatus.NOT_FOUND, f"device not found: {exc.args[0]}")
        except (ValueError, TypeError) as exc:
            return self._error(HTTPStatus.BAD_REQUEST, str(exc))

    def do_PUT(self) -> None:  # noqa: N802
        route = urlparse(self.path).path
        try:
            payload = self._body()
            if route.startswith("/api/v1/users/"):
                if not self._require_role("admin"):
                    return
                user = APP.store.update_user(route.rsplit("/", 1)[-1], payload)
                return self._json({"ok": True, "user": user})
            if not self._require_role("operator"):
                return
            match = re.fullmatch(r"/api/v1/scenes/([a-z0-9-]+)", route)
            if match:
                scene = APP.store.update_scene(match.group(1), payload)
                event = APP.store.add_event("operator", "content.scene_updated", payload={"sceneId": scene["id"]})
                APP.broker.publish("dashboard", {"event": "field-event", "data": event})
                return self._json({"ok": True, "scene": scene})
            match = re.fullmatch(r"/api/v1/devices/([a-z0-9-]+)/location", route)
            if match:
                device = APP.store.update_device_location(match.group(1), str(payload.get("locationName", "")))
                event = APP.store.add_event("operator", "device.location_updated", subject_id=device["id"], zone_id=device["zone_id"])
                APP.broker.publish("dashboard", {"event": "field-event", "data": event})
                return self._json({"ok": True, "device": device})
            match = re.fullmatch(r"/api/v1/devices/([a-z0-9-]+)", route)
            if match:
                device = APP.store.update_device(match.group(1), payload)
                event = APP.store.add_event("operator", "device.profile_updated", subject_id=device["id"], zone_id=device["zone_id"])
                APP.broker.publish("dashboard", {"event": "field-event", "data": event})
                return self._json({"ok": True, "device": device})
            return self._error(HTTPStatus.NOT_FOUND, "unknown API endpoint")
        except (ValueError, TypeError) as exc:
            return self._error(HTTPStatus.BAD_REQUEST, str(exc))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run BaiPlayer 1.0 locally")
    parser.add_argument("--bind", default=os.getenv("BAIPLAYER_BIND", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("BAIPLAYER_PORT", "4340")))
    parser.add_argument("--aos", default=os.getenv("BAIPLAYER_AOS_BASE_URL", ""), help="AOS base URL; GET-only probe")
    parser.add_argument("--rotate-initial-admin", action="store_true", help="rotate bootstrap admin password and exit")
    return parser.parse_args()


def main() -> None:
    global APP
    args = parse_args()
    if args.rotate_initial_admin:
        credential_path = Store(DB_PATH).rotate_initial_admin()
        print(f"Bootstrap administrator credential rotated: {credential_path}")
        return
    APP = BaiPlayerApp(RuntimeConfig(
        bind=args.bind, port=args.port, aos_base_url=args.aos,
        api_token=os.getenv("BAIPLAYER_API_TOKEN", ""),
        allow_simulation_writes=os.getenv("BAIPLAYER_ALLOW_SIMULATION_WRITES", "1") == "1",
        real_device_output=os.getenv("BAIPLAYER_REAL_DEVICE_OUTPUT", "0") == "1",
        public_base_url=os.getenv("BAIPLAYER_PUBLIC_BASE_URL", ""),
    ))
    server = ThreadingHTTPServer((APP.config.bind, APP.config.port), Handler)
    print(f"BaiPlayer {VERSION} · {APP.config.mode} · http://{APP.config.bind}:{APP.config.port}")
    print(("真实设备输出已启用。" if APP.config.real_device_output else "真实设备输出锁定。") + " AOS探测仅使用GET。按 Ctrl+C 停止。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
