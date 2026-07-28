import threading
import time
from datetime import datetime, timezone

import requests

from services.local_json_service import DATA_DIR, LocalJsonStore


def utc_now():
    return datetime.now(timezone.utc).isoformat()


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
                "status": "Acquiring Signal",
                "message": "Searching for GPS signal...",
                "latitude": None,
                "longitude": None,
                "address": None,
                "speed_kmh": 0,
                "accuracy_m": None,
                "source": "Unavailable",
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
            "accuracy_m": latest.get("accuracy_m"),
            "source": latest.get("source", "Mock"),
            "last_updated": latest.get("timestamp"),
            "video_id": latest.get("video_id"),
        }

    def add_point(
        self,
        latitude,
        longitude,
        speed_kmh=0,
        video_id=None,
        address=None,
        device_id="roadwatch_local_01",
        accuracy_m=None,
        source="Browser",
    ):
        points = self.list_points()
        point = {
            "device_id": device_id,
            "timestamp": utc_now(),
            "latitude": latitude,
            "longitude": longitude,
            "address": address,
            "speed_kmh": speed_kmh,
            "accuracy_m": accuracy_m,
            "source": source,
            "video_id": video_id,
        }
        points.append(point)
        self.store.write(points)
        return point

    def add_browser_point(self, latitude, longitude, speed_kmh=0, video_id=None, address=None, device_id="roadwatch_local_01", accuracy_m=None):
        if not -90 <= float(latitude) <= 90 or not -180 <= float(longitude) <= 180:
            raise ValueError("Browser geolocation returned invalid coordinates.")
        latest = self.latest()
        if latest and latest.get("latitude") == latitude and latest.get("longitude") == longitude and latest.get("video_id") == video_id:
            return latest
        return self.add_point(
            latitude,
            longitude,
            speed_kmh,
            video_id,
            address,
            device_id,
            accuracy_m=accuracy_m,
            source="Browser",
        )

    def reverse_geocode(self, latitude, longitude):
        response = requests.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={"lat": latitude, "lon": longitude, "format": "jsonv2"},
            headers={"User-Agent": "RoadwatchVisionRecorder/1.0"},
            timeout=10,
        )
        response.raise_for_status()
        return response.json().get("display_name")


class LocationTrackingService:
    """Background location monitor for the Streamlit app lifecycle.

    Desktop Streamlit cannot directly trigger browser GPS permissions from
    Python, so this service keeps the subsystem alive, reports friendly status,
    and records points when a provider/mock point is available.
    """

    def __init__(self, settings):
        self.gps = GPSService()
        self.settings = dict(settings)
        self.device_id = settings.get("device_id", "roadwatch_local_01")
        self.current_video_id = None
        self._status = self._initial_status()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="roadwatch-location-tracker",
        )
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def update_settings(self, settings):
        with self._lock:
            self.settings = dict(settings)
            self.device_id = settings.get("device_id", self.device_id)

    def link_video(self, video_id):
        with self._lock:
            self.current_video_id = video_id
            self._status["video_id"] = video_id

    def unlink_video(self):
        with self._lock:
            self.current_video_id = None
            self._status["video_id"] = None

    def status(self):
        with self._lock:
            status = dict(self._status)
            current_video_id = self.current_video_id
        if not status.get("last_updated"):
            latest = self.gps.status()
            status.update(latest)
            status["video_id"] = current_video_id or status.get("video_id")
        return status

    def _run(self):
        while not self._stop.wait(2):
            with self._lock:
                enabled = bool(self.settings.get("location_tracking_enabled", True))
                save_history = bool(self.settings.get("location_save_history", True))
                high_accuracy = bool(self.settings.get("location_high_accuracy", False))
                show_address = bool(self.settings.get("location_show_address", True))
                video_id = self.current_video_id
            if not enabled:
                self._set_status(
                    "Unavailable",
                    "Location services disabled.",
                    source="Disabled",
                    video_id=video_id,
                )
                continue
            latest = self.gps.latest()
            if latest:
                point = dict(latest)
                original_video_id = point.get("video_id")
                point["video_id"] = video_id
                if not show_address:
                    point["address"] = None
                self._set_status(
                    "Active",
                    "Location tracking active.",
                    point,
                    source=point.get("source", "Mock"),
                    accuracy_m=point.get("accuracy_m") or (10 if high_accuracy else 25),
                    video_id=point.get("video_id"),
                )
                if save_history and video_id and original_video_id != video_id:
                    self.gps.add_point(
                        point.get("latitude"),
                        point.get("longitude"),
                        point.get("speed_kmh", 0),
                        video_id=video_id,
                        address=point.get("address"),
                        device_id=self.device_id,
                        accuracy_m=point.get("accuracy_m") or (10 if high_accuracy else 25),
                        source=point.get("source", "Mock"),
                    )
            else:
                self._set_status(
                    "Acquiring Signal",
                    "Searching for GPS signal...",
                    source="Unavailable",
                    video_id=video_id,
                )

    def _initial_status(self):
        if not self.settings.get("location_tracking_enabled", True):
            return {
                "status": "Unavailable",
                "message": "Location services disabled.",
                "latitude": None,
                "longitude": None,
                "address": None,
                "speed_kmh": 0,
                "accuracy_m": None,
                "source": "Disabled",
                "last_updated": None,
                "video_id": None,
            }
        return self.gps.status()

    def _set_status(
        self,
        status,
        message,
        point=None,
        source="Unavailable",
        accuracy_m=None,
        video_id=None,
    ):
        point = point or {}
        with self._lock:
            self._status = {
                "status": status,
                "message": message,
                "latitude": point.get("latitude"),
                "longitude": point.get("longitude"),
                "address": point.get("address"),
                "speed_kmh": point.get("speed_kmh", 0),
                "accuracy_m": accuracy_m,
                "source": source,
                "last_updated": utc_now(),
                "video_id": video_id,
            }
