from datetime import datetime, timezone
from uuid import uuid4

from services.local_json_service import DATA_DIR, LocalJsonStore


INCIDENT_TYPES = [
    "movement detected",
    "person detected",
    "vehicle detected",
    "plate detected",
    "possible stolen vehicle match",
    "AI processing error",
    "manual incident",
]


class IncidentService:
    def __init__(self):
        self.store = LocalJsonStore(
            DATA_DIR / "incidents" / "incidents.json", []
        )

    def list_incidents(self):
        return sorted(
            self.store.read(),
            key=lambda item: item.get("timestamp", ""),
            reverse=True,
        )

    def save_all(self, incidents):
        return self.store.write(incidents)

    def create_manual(self, video_id, description, severity="medium"):
        incidents = self.list_incidents()
        incident = {
            "incident_id": f"inc_manual_{uuid4().hex[:8]}",
            "linked_video_id": video_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": "manual incident",
            "severity": severity,
            "description": description,
            "plate_text": None,
            "object_labels": [],
            "video_path": None,
            "processed_video_path": None,
            "status": "open",
        }
        incidents.append(incident)
        self.save_all(incidents)
        return incident

    def update_status(self, incident_id, status):
        incidents = self.list_incidents()
        for incident in incidents:
            if incident.get("incident_id") == incident_id:
                incident["status"] = status
        self.save_all(incidents)

    def sync_generated(self, videos, stolen_reports=None):
        stolen_reports = stolen_reports or []
        stolen_plates = {
            item.get("plate_number", "").upper(): item
            for item in stolen_reports
            if item.get("plate_number")
            and item.get("status") == "Active Alert"
        }
        incidents = {
            item.get("incident_id"): item for item in self.list_incidents()
        }
        for video in videos:
            if video.get("processing_error"):
                self._upsert(
                    incidents,
                    self._incident(
                        video,
                        "AI processing error",
                        "high",
                        video.get("processing_error"),
                    ),
                )
            for event in video.get("detections", []):
                labels = sorted(
                    {
                        item.get("label")
                        for item in event.get("objects", [])
                        if item.get("label")
                    }
                )
                if event.get("movement_detected"):
                    self._upsert(
                        incidents,
                        self._incident(
                            video,
                            "movement detected",
                            "low",
                            "Movement was detected during recording.",
                            event,
                            labels,
                        ),
                    )
                if event.get("people_count", 0) > 0:
                    self._upsert(
                        incidents,
                        self._incident(
                            video,
                            "person detected",
                            "medium",
                            "Person detected by AI scanner.",
                            event,
                            labels,
                        ),
                    )
                if event.get("vehicle_count", 0) > 0:
                    self._upsert(
                        incidents,
                        self._incident(
                            video,
                            "vehicle detected",
                            "medium",
                            "Vehicle detected by AI scanner.",
                            event,
                            labels,
                        ),
                    )
                plate = (event.get("plate_text") or "").upper()
                if plate:
                    self._upsert(
                        incidents,
                        self._incident(
                            video,
                            "plate detected",
                            "medium",
                            f"Plate detected: {plate}",
                            event,
                            labels,
                            plate,
                        ),
                    )
                    if plate in stolen_plates:
                        self._upsert(
                            incidents,
                            self._incident(
                                video,
                                "possible stolen vehicle match",
                                "high",
                                f"Detected plate matches stolen report: {plate}",
                                event,
                                labels,
                                plate,
                            ),
                        )
        self.save_all(list(incidents.values()))
        return self.list_incidents()

    @staticmethod
    def _upsert(incidents, incident):
        existing = incidents.get(incident["incident_id"], {})
        incident["status"] = existing.get("status", incident["status"])
        incidents[incident["incident_id"]] = {**existing, **incident}

    @staticmethod
    def _incident(
        video, incident_type, severity, description, event=None,
        labels=None, plate_text=None
    ):
        event = event or {}
        labels = labels or []
        frame = event.get("frame_number", "video")
        plate_key = plate_text or "none"
        incident_id = (
            f'inc_{video.get("video_id")}_{incident_type.replace(" ", "_")}'
            f"_{frame}_{plate_key}"
        )
        return {
            "incident_id": incident_id,
            "linked_video_id": video.get("video_id"),
            "timestamp": event.get("timestamp") or video.get("started_at"),
            "type": incident_type,
            "severity": severity,
            "description": description,
            "plate_text": plate_text,
            "object_labels": labels,
            "video_path": video.get("original_video_path") or video.get("video_path"),
            "processed_video_path": video.get("processed_video_path"),
            "status": "open",
        }
