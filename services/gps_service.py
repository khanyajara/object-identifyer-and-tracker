from datetime import datetime, timezone

from services.local_json_service import DATA_DIR, LocalJsonStore


class GPSService:
    def __init__(self):
        self.store = LocalJsonStore(DATA_DIR / "gps" / "gps_history.json", [])

    def list_points(self):
        return sorted(
            self.store.read(),
            key=lambda item: item.get("timestamp", ""),
            reverse=True,
        )

    def latest(self):
        points = self.list_points()
        return points[0] if points else None

    def status(self):
        latest = self.latest()
        return {
            "status": "Offline" if latest is None else "Local/mock",
            "latitude": latest.get("latitude") if latest else None,
            "longitude": latest.get("longitude") if latest else None,
            "speed_kmh": latest.get("speed_kmh", 0) if latest else 0,
        }

    def add_mock_point(self, latitude, longitude, speed_kmh=0, video_id=None):
        points = self.list_points()
        point = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "latitude": latitude,
            "longitude": longitude,
            "speed_kmh": speed_kmh,
            "video_id": video_id,
        }
        points.append(point)
        self.store.write(points)
        return point
