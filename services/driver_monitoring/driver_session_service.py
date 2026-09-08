"""Temporal confirmation, expiry and public identity snapshots independent of UI."""
import threading
import time
from dataclasses import asdict
from models.driver_identity import DriverSession, utc_now


class DriverSessionService:
    def __init__(self, config, clock=time.monotonic):
        self.config, self.clock = config, clock
        self.session = DriverSession()
        self._lock = threading.RLock()
        self._last_seen = None
        self._candidate = None
        self._matches = 0
        self._last_sample = None
        self._mismatch_id = None
        self._mismatches = 0

    def context(self, **fields):
        with self._lock:
            for key in ("vehicle_id", "video_id", "trip_id"):
                if key in fields:
                    setattr(self.session, key, fields[key])

    def invalidate(self, status="unknown"):
        with self._lock:
            self.session.driver_id = None
            self.session.driver_display_name = "Unknown Driver"
            self.session.recognition_confidence = 0.0
            self.session.identity_status = status
            self._candidate, self._matches = None, 0
            self._mismatch_id, self._mismatches = None, 0
            self.session.verified_at = None

    def observe(self, visible, now=None):
        now = self.clock() if now is None else now
        with self._lock:
            if self._last_seen is not None and now - self._last_seen >= self.config.lost_timeout:
                self.invalidate("lost")
            if visible:
                self._last_seen = now
                self.session.last_seen_at = utc_now()
                if self.session.identity_status in {"no_face", "lost"}:
                    self.session.identity_status = "detecting"
            else:
                # Keep a verified identity through a brief occlusion. Expiry above
                # clears it even without any subsequent camera frame.
                if self.session.identity_status != "verified":
                    self.invalidate("lost" if self._last_seen is not None and now - self._last_seen >= self.config.lost_timeout else "no_face")

    @property
    def revalidation_pending(self):
        with self._lock:
            return self._mismatches > 0

    def match(self, match, now=None):
        now = self.clock() if now is None else now
        with self._lock:
            if self._last_sample is not None and now - self._last_sample < self.config.candidate_interval:
                return
            self._last_sample = now
            if self.session.identity_status == "verified":
                other_id = match["driver_id"] if match else "unknown"
                if match and other_id == self.session.driver_id:
                    self._mismatches, self._mismatch_id = 0, None
                    self.session.recognition_confidence = match["confidence"]
                    return
                # Any repeated disagreement with the active identity counts; an
                # alternating unknown/other-driver result must not retain it forever.
                self._mismatches += 1
                self._mismatch_id = other_id
                if self._mismatches < self.config.required_matches:
                    return
                self.invalidate()
            if match is None:
                self.invalidate()
                return
            driver_id = match["driver_id"]
            if driver_id != self._candidate:
                self.invalidate("candidate")
                self._candidate = driver_id
            self._matches += 1
            self.session.identity_status = "candidate"
            if self._matches >= self.config.required_matches:
                previous = self.session
                self.session = DriverSession(
                    driver_id=driver_id, identity_status="verified", last_seen_at=previous.last_seen_at,
                    vehicle_id=previous.vehicle_id, video_id=previous.video_id, trip_id=previous.trip_id,
                    recognition_confidence=match["confidence"], driver_display_name=match["display_name"],
                    verified_at=utc_now(),
                )

    def snapshot(self, now=None):
        now = self.clock() if now is None else now
        with self._lock:
            if self._last_seen is not None and now - self._last_seen >= self.config.lost_timeout:
                self.invalidate("lost")
            return asdict(self.session)

    def metadata(self):
        data = self.snapshot()
        return {"driver_id": data["driver_id"], "driver_identity_status": "verified" if data["identity_status"] == "verified" else "unknown", "driver_session_id": data["session_id"]}
