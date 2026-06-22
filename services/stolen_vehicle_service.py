from datetime import datetime, timezone
from uuid import uuid4

from services.local_json_service import DATA_DIR, LocalJsonStore


class StolenVehicleService:
    def __init__(self):
        self.store = LocalJsonStore(
            DATA_DIR / "stolen_vehicles" / "stolen_vehicles.json", []
        )

    def list_reports(self):
        return sorted(
            self.store.read(),
            key=lambda item: item.get("created_at", ""),
            reverse=True,
        )

    def add_report(self, payload):
        reports = self.list_reports()
        payload = {
            **payload,
            "report_id": f"stolen_{uuid4().hex[:8]}",
            "plate_number": payload.get("plate_number", "").upper().strip(),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        reports.append(payload)
        self.store.write(reports)
        return payload

    def search(self, query):
        query = query.upper().strip()
        return [
            item for item in self.list_reports()
            if query in item.get("plate_number", "").upper()
        ] if query else self.list_reports()

    def match_videos(self, videos):
        reports = {
            item.get("plate_number", "").upper(): item
            for item in self.list_reports()
        }
        matches = []
        for video in videos:
            for event in video.get("detections", []):
                plate = (event.get("plate_text") or "").upper()
                if plate and plate in reports:
                    matches.append(
                        {
                            "matched_plate": plate,
                            "confidence": 100,
                            "linked_video": video.get("video_id"),
                            "timestamp": event.get("timestamp"),
                            "report_status": reports[plate].get("status"),
                            "case_reference": reports[plate].get("case_reference"),
                        }
                    )
        return matches
