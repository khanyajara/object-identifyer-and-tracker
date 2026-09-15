import json
from collections import Counter
from datetime import datetime, timezone

import cv2

from services.video_service import LOGS_DIR, SNAPSHOTS_DIR


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
            summary["people_count_max"] = max(
                summary["people_count_max"], event["people_count"]
            )
            summary["vehicle_count_max"] = max(
                summary["vehicle_count_max"], event["vehicle_count"]
            )
            if event["movement_detected"] and not self._previous_movement:
                summary["movement_events"] += 1
            self._previous_movement = event["movement_detected"]
            frame_labels = []
            for item in event["objects"]:
                item["video_id"] = self.record["video_id"]
                tracking_id = item.get("tracking_id")
                if tracking_id is not None:
                    # A class fluctuation must not count one ID as two objects.
                    label = self._tracked_labels.setdefault(int(tracking_id), item["label"])
                else:
                    label = item["label"]
                frame_labels.append(label)
            frame_counts = Counter(frame_labels)
            summary["unique_tracking_ids"] = sorted(self._tracked_labels)
            tracked_counts = Counter(self._tracked_labels.values())
            for label, count in frame_counts.items():
                summary["object_counts"][label] = max(
                    summary["object_counts"].get(label, 0),
                    count,
                    tracked_counts.get(label, 0),
                )
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
        self.record["objects_summary"] = {
            "people_count_max": 0,
            "vehicle_count_max": 0,
            "plates_detected": [],
            "movement_events": 0,
            "object_counts": {},
            "unique_tracking_ids": [],
            "snapshots": 0,
        }
        self._previous_movement = False
        self._tracked_labels = {}
        try:
            self.events_path.unlink()
        except FileNotFoundError:
            pass
        self.add_many(entries)
