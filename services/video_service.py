import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


PROJECT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_DIR / "data"
VIDEOS_DIR = DATA_DIR / "videos"
LOGS_DIR = DATA_DIR / "logs"
SNAPSHOTS_DIR = DATA_DIR / "snapshots"
EXPORTS_DIR = DATA_DIR / "exports"


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class VideoService:
    def __init__(self):
        for path in (VIDEOS_DIR, LOGS_DIR, SNAPSHOTS_DIR, EXPORTS_DIR):
            path.mkdir(parents=True, exist_ok=True)

    def create_record(self, camera_id, fps, resolution):
        now = datetime.now()
        video_id = f"vid_{now:%Y%m%d_%H%M%S}_{uuid4().hex[:6]}"
        filename = f"recording_{now:%Y_%m_%d_%H%M%S}.mp4"
        record = {
            "video_id": video_id,
            "filename": filename,
            "video_path": str(VIDEOS_DIR / filename),
            "started_at": utc_now(),
            "ended_at": None,
            "duration_seconds": 0,
            "camera_id": camera_id,
            "fps": float(fps),
            "resolution": resolution,
            "recording_status": "Recording",
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
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(record, indent=2), encoding="utf-8")
        temporary.replace(path)

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
