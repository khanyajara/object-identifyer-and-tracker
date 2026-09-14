import re
from pathlib import Path
from uuid import uuid4

from services.local_json_service import DATA_DIR, LocalJsonStore

from core.time_utils import utc_now


REPORT_STATUSES = [
    "Draft",
    "Awaiting Images",
    "Awaiting Ownership Documents",
    "Submitted",
    "Under Admin Review",
    "Active Alert",
    "Rejected",
    "Located",
    "Closed",
]

REQUIRED_DETAIL_FIELDS = [
    "plate_number",
    "make",
    "model",
    "year",
    "colour",
    "last_seen_location",
    "last_seen_datetime",
    "contact_number",
]

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
DOCUMENT_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}


class StolenVehicleService:
    def __init__(self):
        self.base_dir = DATA_DIR / "stolen_vehicles"
        self.images_dir = self.base_dir / "images"
        self.documents_dir = self.base_dir / "documents"
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.documents_dir.mkdir(parents=True, exist_ok=True)
        self.store = LocalJsonStore(
            self.base_dir / "stolen_vehicles.json", []
        )

    def list_reports(self):
        return sorted(
            self.store.read(),
            key=lambda item: item.get("created_at", ""),
            reverse=True,
        )

    def get_report(self, report_id):
        return next(
            (
                item for item in self.list_reports()
                if item.get("report_id") == report_id
            ),
            None,
        )

    def save_report(self, payload):
        reports = self.list_reports()
        report_id = payload.get("report_id") or f"stolen_{uuid4().hex[:8]}"
        existing = self.get_report(report_id) or {}
        payload = {
            **existing,
            **self._normalise_payload(payload),
            "report_id": report_id,
            "device_id": payload.get("device_id", "roadwatch_local_01"),
            "created_at": existing.get("created_at") or utc_now(),
            "updated_at": utc_now(),
            "image_paths": existing.get("image_paths", payload.get("image_paths", [])),
            "document_paths": existing.get("document_paths", payload.get("document_paths", [])),
        }
        payload["missing_required_fields"] = self.missing_required_fields(payload)
        if payload.get("status") not in REPORT_STATUSES:
            payload["status"] = self.next_incomplete_status(payload)
        if payload.get("status") == "Awaiting Images":
            detail_missing = [
                field for field in REQUIRED_DETAIL_FIELDS
                if not str(payload.get(field, "")).strip()
            ]
            if detail_missing:
                payload["status"] = "Draft"
        if payload.get("status") == "Awaiting Ownership Documents" and (
            not payload.get("image_paths")
            or any(
                not str(payload.get(field, "")).strip()
                for field in REQUIRED_DETAIL_FIELDS
            )
        ):
            payload["status"] = self.next_draft_status(payload)
        reports = [item for item in reports if item.get("report_id") != report_id]
        reports.append(payload)
        self.store.write(reports)
        return payload

    def update_status(self, report_id, status, reason=None):
        report = self.get_report(report_id)
        if not report:
            raise ValueError(f"Unknown stolen vehicle report: {report_id}")
        if status not in REPORT_STATUSES:
            raise ValueError(f"Unsupported stolen vehicle status: {status}")
        if status == "Active Alert" and not self.can_activate(report):
            missing = ", ".join(self.missing_required_fields(report))
            raise ValueError(
                "Cannot mark report as Active Alert until required "
                f"details, images, and ownership documents are complete: {missing}"
            )
        report["status"] = status
        report["updated_at"] = utc_now()
        if reason:
            report["review_reason"] = reason
        return self.save_report(report)

    def submit_report(self, report_id):
        report = self.get_report(report_id)
        if not report:
            raise ValueError(f"Unknown stolen vehicle report: {report_id}")
        missing = self.missing_required_fields(report)
        if missing:
            report["status"] = self.next_incomplete_status(report)
            report["missing_required_fields"] = missing
            self.save_report(report)
            return report, missing
        report["status"] = "Submitted"
        report["submitted_at"] = utc_now()
        return self.save_report(report), []

    def save_uploads(self, report_id, files, kind):
        report = self.get_report(report_id)
        if not report:
            raise ValueError(f"Unknown stolen vehicle report: {report_id}")
        if kind == "image":
            target_dir = self.images_dir / report_id
            allowed = IMAGE_EXTENSIONS
            key = "image_paths"
        elif kind == "document":
            target_dir = self.documents_dir / report_id
            allowed = DOCUMENT_EXTENSIONS
            key = "document_paths"
        else:
            raise ValueError("Upload kind must be 'image' or 'document'.")
        target_dir.mkdir(parents=True, exist_ok=True)
        saved = []
        for file_obj in files or []:
            name = Path(file_obj.name).name
            suffix = Path(name).suffix.lower()
            if suffix not in allowed:
                continue
            clean_name = self._safe_filename(name)
            path = target_dir / f"{uuid4().hex[:8]}_{clean_name}"
            path.write_bytes(file_obj.getbuffer())
            saved.append(str(path))
        if saved:
            existing = list(report.get(key, []))
            report[key] = existing + saved
            report["status"] = self.next_draft_status(report)
            report["updated_at"] = utc_now()
            self.save_report(report)
        return saved

    def missing_required_fields(self, report):
        missing = [
            field for field in REQUIRED_DETAIL_FIELDS
            if not str(report.get(field, "")).strip()
        ]
        if not report.get("image_paths"):
            missing.append("vehicle_images")
        if not report.get("document_paths"):
            missing.append("ownership_documents")
        return missing

    def can_activate(self, report):
        return not self.missing_required_fields(report)

    def next_incomplete_status(self, report):
        detail_missing = [
            field for field in REQUIRED_DETAIL_FIELDS
            if not str(report.get(field, "")).strip()
        ]
        if detail_missing:
            return "Draft"
        if not report.get("image_paths"):
            return "Awaiting Images"
        if not report.get("document_paths"):
            return "Awaiting Ownership Documents"
        return report.get("status") if report.get("status") in {
            "Submitted",
            "Under Admin Review",
            "Active Alert",
            "Rejected",
            "Located",
            "Closed",
        } else "Draft"

    def next_draft_status(self, report):
        detail_missing = [
            field for field in REQUIRED_DETAIL_FIELDS
            if not str(report.get(field, "")).strip()
        ]
        if detail_missing:
            return "Draft"
        if not report.get("image_paths"):
            return "Awaiting Images"
        if not report.get("document_paths"):
            return "Awaiting Ownership Documents"
        return "Draft"

    def search(self, query):
        query = query.upper().strip()
        if not query:
            return self.list_reports()
        results = []
        for item in self.list_reports():
            haystack = " ".join(
                str(item.get(field, ""))
                for field in (
                    "report_id",
                    "plate_number",
                    "make",
                    "model",
                    "year",
                    "colour",
                    "vin",
                    "last_seen_location",
                    "case_reference",
                    "contact_number",
                    "status",
                )
            ).upper()
            if query in haystack:
                results.append(item)
        return results

    def match_videos(self, videos):
        reports = {
            item.get("plate_number", "").upper(): item
            for item in self.list_reports()
            if item.get("status") == "Active Alert"
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

    @staticmethod
    def _normalise_payload(payload):
        return {
            "plate_number": payload.get("plate_number", "").upper().strip(),
            "make": payload.get("make", payload.get("vehicle_make", "")).strip(),
            "model": payload.get("model", payload.get("vehicle_model", "")).strip(),
            "year": str(payload.get("year", "")).strip(),
            "colour": payload.get("colour", "").strip(),
            "vin": payload.get("vin", "").strip(),
            "last_seen_location": payload.get("last_seen_location", "").strip(),
            "last_seen_datetime": str(payload.get("last_seen_datetime", "")).strip(),
            "case_reference": payload.get("case_reference", "").strip(),
            "contact_number": payload.get("contact_number", "").strip(),
            "notes": payload.get("notes", payload.get("owner_contact_note", "")).strip(),
            "status": payload.get("status", "Draft"),
        }

    @staticmethod
    def _safe_filename(name):
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", name.strip())
        return name.strip("._") or "upload"
