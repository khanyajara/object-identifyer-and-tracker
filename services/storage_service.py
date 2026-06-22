from services.local_json_service import DATA_DIR
from services.video_service import (
    EXPORTS_DIR,
    LOGS_DIR,
    PROCESSED_VIDEOS_DIR,
    SNAPSHOTS_DIR,
    VIDEOS_DIR,
)


class StorageService:
    def paths(self):
        return {
            "data": DATA_DIR,
            "videos": VIDEOS_DIR,
            "processed_videos": PROCESSED_VIDEOS_DIR,
            "logs": LOGS_DIR,
            "snapshots": SNAPSHOTS_DIR,
            "exports": EXPORTS_DIR,
            "incidents": DATA_DIR / "incidents",
            "stolen_vehicles": DATA_DIR / "stolen_vehicles",
            "gps": DATA_DIR / "gps",
            "contacts": DATA_DIR / "contacts",
            "profile": DATA_DIR / "profile",
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
