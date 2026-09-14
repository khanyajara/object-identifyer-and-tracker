"""Retry changed local app records in a background worker; no camera-thread network I/O."""
import atexit
import hashlib
import json
import logging
import os
from pathlib import Path
import sqlite3
import threading
import uuid
from urllib.parse import quote

import requests
from services.supabase_database_service import SupabaseDatabaseService

DATA_ROOT = Path(__file__).resolve().parents[1] / "data"
RECORD_DIRS = ("logs", "videos", "incidents", "gps", "contacts", "profile", "stolen_vehicles", "missing_persons", "fatigue_events", "notifications", "uploads")
ASSET_DIRS = ("snapshots", "exports", "stolen_vehicles", "missing_persons", "videos/thumbnails")
MIME_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".pdf": "application/pdf", ".csv": "text/csv"}
PRIVATE_KEYS = {"password", "password_hash", "embedding", "embeddings", "landmarks", "raw_landmarks", "private_key", "api_key", "apikey", "access_token", "refresh_token", "smtp_password", "webhook_url"}


def clean_payload(value):
    if isinstance(value, dict):
        return {key: clean_payload(item) for key, item in value.items()
                if key.lower() not in PRIVATE_KEYS and not any(s in key.lower() for s in ("secret", "password", "credential"))}
    if isinstance(value, list):
        return [clean_payload(item) for item in value]
    return value


class SupabaseSyncService:
    def __init__(self, root=None, cloud=None):
        self.root = Path(root or DATA_ROOT).resolve()
        self.cloud = cloud or SupabaseDatabaseService()
        self.state_dir = self.root / "cloud_sync"
        self.status = {"uploaded": 0, "errors": 0, "running": False}
        self._lock = threading.Lock()

    def _state(self):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.state_dir / "state.sqlite3", timeout=10)
        connection.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, digest TEXT NOT NULL)")
        row = connection.execute("SELECT digest FROM state WHERE key='device_id'").fetchone()
        if row is None:
            device_id = os.getenv("ROADWATCH_DEVICE_ID") or uuid.uuid4().hex
            connection.execute("INSERT INTO state VALUES ('device_id', ?)", (device_id,))
            connection.commit()
        else:
            device_id = row[0]
        return connection, device_id

    def files(self):
        seen = set()
        for directory in RECORD_DIRS + ASSET_DIRS:
            for path in (self.root / directory).rglob("*"):
                if not path.is_file() or not path.resolve().is_relative_to(self.root):
                    continue
                rel = path.relative_to(self.root).as_posix()
                if rel in seen or path.name == "notification_settings.json":
                    continue
                is_record = path.suffix.lower() in {".json", ".jsonl"} and directory in RECORD_DIRS
                is_asset = path.suffix.lower() in MIME_TYPES and directory in ASSET_DIRS
                if is_record or is_asset:
                    seen.add(rel)
                    yield path, rel, is_record

    def upload_asset(self, path, rel, device_id):
        bucket = os.getenv("SUPABASE_ASSET_BUCKET", "roadwatch-assets")
        name = device_id + "/" + rel
        headers = {"apikey": self.cloud.key, "Content-Type": MIME_TYPES[path.suffix.lower()], "x-upsert": "true"}
        if not self.cloud.key.startswith("sb_"):
            headers["Authorization"] = "Bearer " + self.cloud.key
        try:
            with path.open("rb") as stream:
                response = requests.post(self.cloud.url + "/storage/v1/object/" + quote(bucket, safe="") + "/" + quote(name, safe="/"), headers=headers, data=stream, timeout=120, allow_redirects=False)
            if not 200 <= response.status_code < 300:
                raise RuntimeError("Attachment upload rejected (HTTP " + str(response.status_code) + ")")
        except requests.RequestException:
            raise RuntimeError("Attachment upload unavailable") from None
        return {"bucket": bucket, "object_path": name, "mime_type": MIME_TYPES[path.suffix.lower()], "size_bytes": path.stat().st_size}

    def run_once(self, limit=100):
        if not self.cloud.enabled:
            return {"enabled": False, "uploaded": 0, "errors": 0}
        with self._lock:
            connection, device_id = self._state()
            uploaded = errors = 0
            try:
                for path, rel, is_record in self.files():
                    if uploaded + errors >= limit:
                        break
                    try:
                        # Streaming hash avoids buffering video-sized assets.
                        with path.open("rb") as stream:
                            digest = hashlib.file_digest(stream, "sha256").hexdigest()
                        key = self.cloud.url + ":" + rel
                        old = connection.execute("SELECT digest FROM state WHERE key=?", (key,)).fetchone()
                        if old and old[0] == digest:
                            continue
                        if is_record:
                            if path.suffix == ".jsonl":
                                # Each log chunk is independently retryable and bounded.
                                chunks = 0
                                with path.open(encoding="utf-8") as stream:
                                    batch = []
                                    for line in stream:
                                        if line.strip():
                                            batch.append(clean_payload(json.loads(line)))
                                        if len(batch) == 100:
                                            self.cloud.upsert_record(device_id, rel + f":chunk:{chunks}", "detection_events", batch)
                                            chunks += 1
                                            batch = []
                                    if batch:
                                        self.cloud.upsert_record(device_id, rel + f":chunk:{chunks}", "detection_events", batch)
                                        chunks += 1
                                payload = {"chunks": chunks, "format": "jsonl"}
                            else:
                                payload = clean_payload(json.loads(path.read_text(encoding="utf-8")))
                        else:
                            payload = self.upload_asset(path, rel, device_id)
                        self.cloud.upsert_record(device_id, rel, rel.split("/")[0] if is_record else "attachment", payload)
                        connection.execute("INSERT OR REPLACE INTO state VALUES (?, ?)", (key, digest))
                        connection.commit()
                        uploaded += 1
                    except (OSError, ValueError, RuntimeError, sqlite3.Error):
                        errors += 1
                self.status = {"enabled": True, "uploaded": uploaded, "errors": errors, "running": True}
                return dict(self.status)
            finally:
                connection.close()


_worker = None
_guard = threading.Lock()
_stop = threading.Event()
_service = None


def sync_video_record(record):
    service = SupabaseSyncService()
    connection, device_id = service._state()
    connection.close()
    service.cloud.upsert_record(device_id, "logs/" + record["video_id"] + ".json", "logs", clean_payload(record))
    return {"configured": True, "backend": "supabase", "synced": True}


def ensure_cloud_sync():
    global _worker, _service
    if not SupabaseDatabaseService().enabled:
        return
    with _guard:
        if _worker is not None and _worker.is_alive():
            return
        _service = SupabaseSyncService()
        def run():
            while not _stop.is_set():
                try:
                    _service.run_once()
                except Exception:
                    _service.status = {"enabled": True, "errors": 1, "running": False}
                    logging.getLogger(__name__).warning("Supabase sync unavailable; local records retained.")
                _stop.wait(30)
        _worker = threading.Thread(target=run, name="supabase-record-sync", daemon=True)
        _worker.start()


def cloud_sync_status():
    return dict(_service.status) if _service is not None else {"enabled": SupabaseDatabaseService().enabled, "running": False, "errors": 0}


atexit.register(_stop.set)
