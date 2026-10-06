import json
import re
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from core.video_io import DEFAULT_VIDEO_EXTENSION
from services.driver_monitoring.runtime import identity_metadata

from core.time_utils import utc_now
from services.local_json_service import _lock_for


from core.storage_paths import PROJECT_DIR, DATA_DIR
VIDEOS_DIR = DATA_DIR / "videos"
PROCESSED_VIDEOS_DIR = VIDEOS_DIR / "processed"
COMPRESSED_VIDEOS_DIR = VIDEOS_DIR / "compressed"
COMPRESSED_PROCESSED_VIDEOS_DIR = PROCESSED_VIDEOS_DIR / "compressed"
THUMBNAILS_DIR = VIDEOS_DIR / "thumbnails"
LOGS_DIR = DATA_DIR / "logs"
SNAPSHOTS_DIR = DATA_DIR / "snapshots"
EXPORTS_DIR = DATA_DIR / "exports"
INCIDENTS_DIR = DATA_DIR / "incidents"
STOLEN_VEHICLES_DIR = DATA_DIR / "stolen_vehicles"
GPS_DIR = DATA_DIR / "gps"
CONTACTS_DIR = DATA_DIR / "contacts"
PROFILE_DIR = DATA_DIR / "profile"
NOTIFICATIONS_DIR = DATA_DIR / "notifications"
VIDEO_ID_PATTERN = re.compile(r"^vid_\d{8}_\d{6}_(?:(?:front|rear)_)?[a-f0-9]{6}$")


class VideoService:
    def __init__(self):
        for path in (
            VIDEOS_DIR,
            PROCESSED_VIDEOS_DIR,
            COMPRESSED_VIDEOS_DIR,
            COMPRESSED_PROCESSED_VIDEOS_DIR,
            THUMBNAILS_DIR,
            LOGS_DIR,
            SNAPSHOTS_DIR,
            EXPORTS_DIR,
            INCIDENTS_DIR,
            STOLEN_VEHICLES_DIR,
            GPS_DIR,
            CONTACTS_DIR,
            PROFILE_DIR,
            NOTIFICATIONS_DIR,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def create_record(self, camera_id, fps, resolution, device_id="roadwatch_local_01", user_id=None):
        now = datetime.now()
        video_id = f"vid_{now:%Y%m%d_%H%M%S}_{uuid4().hex[:6]}"
        filename = f"recording_{now:%Y_%m_%d_%H%M%S}_{video_id.rsplit('_', 1)[1]}{DEFAULT_VIDEO_EXTENSION}"
        video_dir = VIDEOS_DIR
        if user_id is not None:
            from services.account_identity import video_owner
            video_owner({"user_id": user_id})
            if any(char in user_id for char in '/\\'):
                raise ValueError("Invalid recording owner")
            video_dir = VIDEOS_DIR / "users" / user_id
        record = {
            **identity_metadata(),
            "video_id": video_id,
            "device_id": device_id,
            "filename": filename,
            "video_format": DEFAULT_VIDEO_EXTENSION.lstrip("."),
            "video_path": str(video_dir / filename),
            "original_video_path": str(video_dir / filename),
            "original_mp4_path": str(video_dir / filename),
            "processed_video_path": None,
            "processed_mp4_path": None,
            "compressed_original_path": None,
            "compressed_original_mp4_path": None,
            "compressed_processed_path": None,
            "compressed_mp4_path": None,
            "playback_video_path": None,
            "playback_source": None,
            "playback_format": None,
            "thumbnail_path": None,
            "thumbnail_status": "not_started",
            "upload_video_path": None,
            "upload_webm_path": None,
            "upload_mp4_path": None,
            "compression_status": "not_started",
            "compression_error": None,
            "supabase_processed_path": None,
            "supabase_processed_url": None,
            "supabase_webm_url": None,
            "supabase_mp4_url": None,
            "supabase_upload_status": "not_uploaded",
            "supabase_upload_error": None,
            "started_at": utc_now(),
            "ended_at": None,
            "duration_seconds": 0,
            "camera_id": camera_id,
            "fps": float(fps),
            "resolution": resolution,
            "recording_status": "Recording",
            "processing_status": "Waiting for recording to finish",
            "processing_error": None,
            "sync_status": "Not synced",
            "objects_summary": {
                "people_count_max": 0,
                "vehicle_count_max": 0,
                "plates_detected": [],
                "movement_events": 0,
                "object_counts": {},
                "unique_tracking_ids": [],
                "snapshots": 0,
            },
            "detections": [],
        }
        if user_id is not None:
            record.update(user_id=user_id, uid=user_id)
        self.save(record)
        from services.driver_monitoring.runtime import recording_context
        recording_context(video_id)
        return record

    def save(self, record):
        path = LOGS_DIR / f'{record["video_id"]}.json'
        with _lock_for(path):
            self._write_json(path, record)
            video_path = Path(
                record.get("original_video_path") or record["video_path"]
            )
            sidecar_path = video_path.with_suffix(".json")
            self._write_json(sidecar_path, record)

    @staticmethod
    def _write_json(path, record):
        with _lock_for(path):
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(path.suffix + ".tmp")
            payload = json.dumps(record, indent=2)
            temporary.write_text(payload, encoding="utf-8")
            try:
                temporary.replace(path)
            except PermissionError:
                # OneDrive can briefly lock a newly-created temporary file.
                path.write_text(payload, encoding="utf-8")
                try:
                    temporary.unlink()
                except OSError:
                    pass

    def load(self, video_id):
        self._validate_video_id(video_id)
        return json.loads(
            (LOGS_DIR / f"{video_id}.json").read_text(encoding="utf-8")
        )

    def list_videos(self):
        records = []
        for path in LOGS_DIR.glob("vid_*.json"):
            try:
                records.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        return sorted(
            records, key=lambda item: item.get("started_at", ""), reverse=True
        )

    def update_sync_fields(self, video_id, **fields):
        self._validate_video_id(video_id)
        with _lock_for(LOGS_DIR / f"{video_id}.json"):
            record = self.load(video_id)
            record.update(fields)
            self.save(record)
        return record

    def ingest_metadata(self, payload):
        """Upsert operator-supplied metadata; never accept remote filesystem paths."""
        import math
        from core.detection_policy import empty_recording_summary, update_recording_summary
        from services.account_identity import video_owner
        from services.supabase_sync_service import clean_payload
        json.dumps(payload, allow_nan=False)
        video_id = payload.get("video_id")
        self._validate_video_id(video_id)
        owner = video_owner(payload)
        if any(char in owner for char in '/\\:'):
            raise ValueError("Invalid recording owner")
        events = payload.get("detections", [])
        if not isinstance(events, list) or any(not isinstance(event, dict) for event in events):
            raise ValueError("detections must be a list of events")
        events = clean_payload(events)
        summary, tracked, previous = empty_recording_summary(), {}, False
        for event in events:
            for key in ("objects", "plates"):
                if not isinstance(event.get(key, []), list) or any(not isinstance(item, dict) for item in event.get(key, [])):
                    raise ValueError(key + " must be a list of objects")
            event["video_id"] = video_id
            previous = update_recording_summary(summary, event, tracked, previous)
            for plate in event.get("plates", []):
                if plate.get("text") and plate["text"] not in summary["plates_detected"]:
                    summary["plates_detected"].append(plate["text"])
        fields = {key: payload[key] for key in ("camera_id", "device_id", "started_at", "ended_at", "resolution") if key in payload}
        if any(value is not None and not isinstance(value, str) for value in fields.values()):
            raise ValueError("Metadata text fields must be strings or null")
        for key in ("duration_seconds", "fps"):
            if key in payload:
                value = float(payload[key])
                if not math.isfinite(value) or value < 0:
                    raise ValueError("Invalid " + key)
                fields[key] = value
        with _lock_for(LOGS_DIR / f"{video_id}.json"):
            try:
                record = self.load(video_id)
                if video_owner(record) != owner:
                    raise ValueError("An existing recording's owner cannot be changed by sync")
            except FileNotFoundError:
                path = VIDEOS_DIR / "users" / owner / (video_id + DEFAULT_VIDEO_EXTENSION)
                record = {"video_id": video_id, "user_id": owner, "uid": owner,
                          "filename": path.name, "video_path": str(path), "original_video_path": str(path),
                          "recording_status": "Imported metadata", "processing_status": "Media not transferred",
                          "started_at": utc_now(), "duration_seconds": 0}
            record.update(fields)
            if "detections" in payload or "detections" not in record:
                record.update(detections=events, objects_summary=summary)
            record.update(sync_status="Metadata received", metadata_received_at=utc_now())
            self.save(record)
        return record

    @staticmethod
    def _validate_video_id(video_id):
        if not isinstance(video_id, str) or not VIDEO_ID_PATTERN.fullmatch(video_id):
            raise ValueError("Invalid video ID.")

    @staticmethod
    def repair_metadata(record):
        """Restore derived MP4 fields when referenced local files still exist."""
        changed = False
        for primary_key, mp4_key, format_key in (
            ("original_video_path", "original_mp4_path", "video_format"),
            ("processed_video_path", "processed_mp4_path", "processed_video_format"),
        ):
            value = record.get(primary_key)
            if not value:
                continue
            path = Path(value)
            if not path.is_file() or not path.stat().st_size:
                continue
            suffix = path.suffix.lower().lstrip(".")
            if record.get(format_key) != suffix:
                record[format_key] = suffix
                changed = True
            if suffix == "mp4" and record.get(mp4_key) != str(path):
                record[mp4_key] = str(path)
                changed = True
        thumbnail = record.get("thumbnail_path")
        if thumbnail and not Path(thumbnail).is_file():
            record["thumbnail_path"] = None
            record["thumbnail_status"] = "missing"
            changed = True
        return record, changed
