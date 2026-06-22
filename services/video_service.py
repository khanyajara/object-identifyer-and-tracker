import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


PROJECT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_DIR / "data"
VIDEOS_DIR = DATA_DIR / "videos"
PROCESSED_VIDEOS_DIR = VIDEOS_DIR / "processed"
LOGS_DIR = DATA_DIR / "logs"
SNAPSHOTS_DIR = DATA_DIR / "snapshots"
EXPORTS_DIR = DATA_DIR / "exports"
INCIDENTS_DIR = DATA_DIR / "incidents"
STOLEN_VEHICLES_DIR = DATA_DIR / "stolen_vehicles"
GPS_DIR = DATA_DIR / "gps"
CONTACTS_DIR = DATA_DIR / "contacts"
PROFILE_DIR = DATA_DIR / "profile"


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class VideoService:
    def __init__(self):
        for path in (
            VIDEOS_DIR,
            PROCESSED_VIDEOS_DIR,
            LOGS_DIR,
            SNAPSHOTS_DIR,
            EXPORTS_DIR,
            INCIDENTS_DIR,
            STOLEN_VEHICLES_DIR,
            GPS_DIR,
            CONTACTS_DIR,
            PROFILE_DIR,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def create_record(self, camera_id, fps, resolution):
        now = datetime.now()
        video_id = f"vid_{now:%Y%m%d_%H%M%S}_{uuid4().hex[:6]}"
        filename = f"recording_{now:%Y_%m_%d_%H%M%S}.mp4"
        record = {
            "video_id": video_id,
            "filename": filename,
            "video_path": str(VIDEOS_DIR / filename),
            "original_video_path": str(VIDEOS_DIR / filename),
            "processed_video_path": None,
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
        self.save(record)
        return record

    def save(self, record):
        path = LOGS_DIR / f'{record["video_id"]}.json'
        self._write_json(path, record)
        video_path = Path(
            record.get("original_video_path") or record["video_path"]
        )
        sidecar_path = video_path.with_suffix(".json")
        self._write_json(sidecar_path, record)

    @staticmethod
    def _write_json(path, record):
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

    def mark_synced(self, video_id, status):
        record = self.load(video_id)
        record["sync_status"] = status
        self.save(record)
        return record

    def update_sync_fields(self, video_id, **fields):
        record = self.load(video_id)
        record.update(fields)
        self.save(record)
        return record
