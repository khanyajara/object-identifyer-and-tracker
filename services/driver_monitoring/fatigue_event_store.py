"""One atomic aggregate record per meaningful fatigue event; no media retention."""
import json
import math
import re
import threading
from datetime import datetime
from pathlib import Path
from services.local_json_service import DATA_DIR

_WRITE_LOCK = threading.Lock()


class FatigueEventStore:
    def __init__(self, directory=None):
        self.directory = Path(directory) if directory is not None else DATA_DIR / "fatigue_events"

    def save(self, event, gps=None):
        event_id = event["event_id"]
        if not re.fullmatch(r"fatigue_[a-f0-9]{32}", event_id):
            raise ValueError("Invalid fatigue event ID")
        keys = ("event_id", "video_id", "trip_id", "vehicle_id", "driver_id", "driver_session_id",
                "driver_identity_status", "timestamp", "video_offset_seconds", "severity", "score", "reasons",
                "perclos", "observed_seconds", "closure_seconds", "blink_count", "yawn_count", "nod_count")
        record = {key: event.get(key) for key in keys}
        record["algorithm_version"] = "temporal-heuristic-v1"
        record["gps"] = None
        if gps:
            try:
                age = (datetime.fromisoformat(event["timestamp"]) - datetime.fromisoformat(gps["timestamp"])).total_seconds()
                lat, lon = float(gps["latitude"]), float(gps["longitude"])
                if 0 <= age <= 15 and gps.get("video_id") == event["video_id"] and math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180:
                    record["gps"] = {key: gps.get(key) for key in ("latitude", "longitude", "timestamp", "accuracy_m", "source", "speed_kmh")}
            except (KeyError, TypeError, ValueError):
                pass
        # One shared lock, not one permanently retained lock per event file.
        with _WRITE_LOCK:
            self.directory.mkdir(parents=True, exist_ok=True)
            target = self.directory / (event_id + ".json")
            temporary = target.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(record, indent=2, allow_nan=False), encoding="utf-8")
            temporary.replace(target)
        return record

    def list_events(self, video_id=None):
        records = []
        for path in self.directory.glob("fatigue_*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                if record.get("event_id") and (video_id is None or record.get("video_id") == video_id):
                    records.append(record)
            except (OSError, ValueError, AttributeError):
                continue
        return sorted(records, key=lambda record: record["timestamp"])

    def incidents(self):
        return [{**event, "incident_id": event["event_id"], "linked_video_id": event["video_id"],
                 "type": "driver fatigue", "description": "Possible driver fatigue: " + ", ".join(event["reasons"]),
                 "status": "open", "plate_text": None, "object_labels": [],
                 "video_path": None, "processed_video_path": None} for event in self.list_events()]
