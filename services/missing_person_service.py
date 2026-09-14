"""Local missing-person case reports with reference photos for manual review."""
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from PIL import Image, UnidentifiedImageError

from core.time_utils import utc_now
from services.local_json_service import DATA_DIR, LocalJsonStore

STATUSES = ("Submitted", "Under Admin Review", "Active Alert", "Located", "Closed", "Rejected")
FIELDS = ("name", "last_seen_location", "last_seen_datetime", "contact_number", "case_reference", "description")


class MissingPersonService:
    def __init__(self, base_dir=None):
        self.base_dir = Path(base_dir or DATA_DIR / "missing_persons")
        self.store = LocalJsonStore(self.base_dir / "reports.json", [])

    def list_reports(self):
        return sorted(self.store.read(), key=lambda row: row["created_at"], reverse=True)

    def submit(self, payload, photo):
        details = {field: str(payload.get(field, "")).strip() for field in FIELDS}
        if any(not details[field] for field in FIELDS[:4]):
            raise ValueError("Name, last-seen location/date and contact number are required.")
        if not photo or len(photo) > 10 * 1024 * 1024:
            raise ValueError("Upload a photo smaller than 10 MB.")
        try:
            with Image.open(BytesIO(photo)) as source:
                if source.width * source.height > 20_000_000:
                    raise ValueError("Photo must be smaller than 20 megapixels.")
                source.load()
                picture = source.convert("RGB")
                picture.thumbnail((1600, 1600))
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise ValueError("Upload a valid JPEG, PNG or WebP photo.") from exc
        report_id = "missing_" + uuid4().hex
        path = self.base_dir / (report_id + ".jpg")
        picture.save(path, "JPEG", quality=90)
        report = dict(details, report_id=report_id, photo_path=str(path), status="Submitted", created_at=utc_now())
        with self.store._lock:
            self.store.write(self.store.read() + [report])
        return report

    def update_status(self, report_id, status):
        if status not in STATUSES:
            raise ValueError("Invalid report status.")
        with self.store._lock:
            reports = self.store.read()
            report = next((row for row in reports if row["report_id"] == report_id), None)
            if report is None:
                raise ValueError("Report not found.")
            report.update(status=status, updated_at=utc_now())
            self.store.write(reports)
