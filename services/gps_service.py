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
        if latest is None:
            return {
                "status": "Unavailable",
                "message": "Location unavailable. Please enable GPS permissions.",
                "latitude": None,
                "longitude": None,
                "address": None,
                "speed_kmh": 0,
                "last_updated": None,
                "video_id": None,
            }
        return {
            "status": "Active",
            "message": "Location tracking active.",
            "latitude": latest.get("latitude"),
            "longitude": latest.get("longitude"),
            "address": latest.get("address") or "Address unavailable",
            "speed_kmh": latest.get("speed_kmh", 0),
            "last_updated": latest.get("timestamp"),
            "video_id": latest.get("video_id"),
        }

    def add_mock_point(self, latitude, longitude, speed_kmh=0, video_id=None, address=None, device_id="roadwatch_local_01"):
        points = self.list_points()
        point = {
            "device_id": device_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "latitude": latitude,
            "longitude": longitude,
            "address": address,
            "speed_kmh": speed_kmh,
            "video_id": video_id,
        }
        points.append(point)
        self.store.write(points)
        return point
