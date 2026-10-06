import json
from datetime import datetime, timezone

import cv2

from services.video_service import LOGS_DIR, SNAPSHOTS_DIR
from core.detection_policy import update_recording_summary, empty_recording_summary


class DetectionLogService:
    def __init__(self, record, save_snapshots=False):
        self.record = record
        self.events_path = LOGS_DIR / f'{record["video_id"]}.jsonl'
        self.snapshot_dir = SNAPSHOTS_DIR / record["video_id"]
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        self.save_snapshots = save_snapshots
        self._previous_movement = False
        self._tracked_labels = {}

    def add_many(self, entries):
        serialized = []
        for event, frame in entries:
            event["video_id"] = self.record["video_id"]
            event.setdefault(
                "timestamp", datetime.now(timezone.utc).isoformat()
            )
            self.record["detections"].append(event)
            summary = self.record["objects_summary"]
            self._previous_movement = update_recording_summary(
                summary, event, self._tracked_labels, self._previous_movement)
            for item in event["objects"]:
                item["video_id"] = self.record["video_id"]
            for plate in event["plates"]:
                text = plate.get("text")
                if text and text not in summary["plates_detected"]:
                    summary["plates_detected"].append(text)
                    if frame is not None and self.save_snapshots:
                        path = self.snapshot_dir / (
                            f'{event["frame_number"]:08d}_{text}.jpg'
                        )
                        cv2.imwrite(str(path), frame)
                        event.setdefault("snapshots", []).append(str(path))
                        summary["snapshots"] += 1
            serialized.append(json.dumps(event))
        if serialized:
            with self.events_path.open("a", encoding="utf-8") as file:
                file.write("\n".join(serialized) + "\n")

    def replace_all(self, entries):
        self.record["detections"] = []
        self.record["objects_summary"] = empty_recording_summary()
        self._previous_movement = False
        self._tracked_labels = {}
        try:
            self.events_path.unlink()
        except FileNotFoundError:
            pass
        self.add_many(entries)
