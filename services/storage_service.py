from datetime import datetime
from pathlib import Path

from services.incident_service import IncidentService
from services.local_json_service import DATA_DIR
from services.stolen_vehicle_service import StolenVehicleService
from services.video_service import (
    EXPORTS_DIR,
    LOGS_DIR,
    PROCESSED_VIDEOS_DIR,
    SNAPSHOTS_DIR,
    THUMBNAILS_DIR,
    VIDEOS_DIR,
    VideoService,
)


class StorageService:
    def paths(self):
        return {
            "data": DATA_DIR,
            "videos": VIDEOS_DIR,
            "processed_videos": PROCESSED_VIDEOS_DIR,
            "thumbnails": THUMBNAILS_DIR,
            "logs": LOGS_DIR,
            "snapshots": SNAPSHOTS_DIR,
            "exports": EXPORTS_DIR,
            "incidents": DATA_DIR / "incidents",
            "stolen_vehicles": DATA_DIR / "stolen_vehicles",
            "gps": DATA_DIR / "gps",
            "contacts": DATA_DIR / "contacts",
            "profile": DATA_DIR / "profile",
            "notifications": DATA_DIR / "notifications",
        }

    def status(self):
        rows = []
        for label, path in self.paths().items():
            path.mkdir(parents=True, exist_ok=True)
            rows.append(
                {
                    "Area": label,
                    "Path": str(path),
                    "Exists": path.exists(),
                }
            )
        return rows

    def cache_cleanup_preview(self, max_items=50):
        """List least-recently-used disposable cache files; evidence is never listed."""
        incidents = IncidentService().list_incidents()
        incident_video_ids = {item.get("video_id") for item in incidents if item.get("video_id")}
        stolen = StolenVehicleService().list_reports()
        stolen_video_ids = {
            video_id
            for report in stolen
            for video_id in report.get("matched_video_ids", []) or []
        }
        rows = []
        for record in VideoService().list_videos():
            video_id = record.get("video_id")
            protected = bool(
                record.get("evidence_protected")
                or record.get("admin_marked")
                or video_id in incident_video_ids
                or video_id in stolen_video_ids
            )
            for key in ("compressed_original_path", "compressed_processed_path", "thumbnail_path"):
                path = Path(record.get(key) or "")
                if not path.is_file():
                    continue
                rows.append({
                    "video_id": video_id,
                    "path": str(path),
                    "kind": key,
                    "bytes": path.stat().st_size,
                    "last_used": path.stat().st_atime,
                    "protected": protected,
                })
        return sorted(rows, key=lambda row: row["last_used"])[:max_items]

    def cleanup_cache(self, max_items=10, dry_run=True):
        candidates = self.cache_cleanup_preview(max_items)
        removable = [row for row in candidates if not row["protected"]]
        if not dry_run:
            for row in removable:
                Path(row["path"]).unlink(missing_ok=True)
        return {"preview": candidates, "removed": removable, "dry_run": dry_run, "ran_at": datetime.now().isoformat()}
