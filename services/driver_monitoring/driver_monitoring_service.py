"""Single-worker orchestration with independent inference schedules."""
import time
import threading
from collections import deque
from datetime import datetime, timezone
from uuid import uuid4
from .biometric_store import BiometricStore
from .driver_session_service import DriverSessionService
from .face_detection_service import FaceDetectionService
from .face_recognition_service import FaceRecognitionService
from .face_landmark_service import FaceLandmarkService, empty_measurements
from .embedding_index import DriverEmbeddingIndex
from .inference_scheduler import InferenceScheduler
from .fatigue_detection_service import FatigueDetectionService, FatigueConfig


class DriverMonitoringService:
    def __init__(self, config, detector=None, recognizer=None, landmarker=None, store=None, clock=time.monotonic):
        self.config, self.clock = config, clock
        self.detector = detector or FaceDetectionService(config)
        self.recognizer = recognizer or FaceRecognitionService(config)
        self.landmarker = landmarker or FaceLandmarkService(config)
        self.store = store or BiometricStore()
        self.index = DriverEmbeddingIndex(self.store, config.index_ttl, config.database_retry_seconds, clock)
        self.sessions = DriverSessionService(config, clock)
        self.scheduler = InferenceScheduler()
        try:
            self.fatigue = FatigueDetectionService()
        except ValueError:
            self.fatigue = FatigueDetectionService(FatigueConfig(enabled=False))
        self.fatigue_events = deque(maxlen=32)
        self.recording_started = None
        self._context_lock = threading.Lock()
        self._fatigue_last_frame = None
        self.active = False
        self._no_face_since = clock()
        self._force_recheck = False
        self._detection_at = self._landmark_at = self._recognition_at = -float("inf")
        self._face = None
        self.measurements = empty_measurements()
        self.error = None
        self.detection_error = None
        self.last_identity_check = None
        self.timings = {}
        self._configure_schedule(clock())

    def set_active(self, active):
        self.active = bool(active)

    def recording_context(self, video_id=None, **fields):
        # Capture context and its time origin atomically, without holding a lock
        # during inference or making the recorder wait on a model/database call.
        with self._context_lock:
            previous = self.sessions.snapshot().get("video_id")
            if video_id != previous:
                self.recording_started = self.clock() if video_id else None
            self.sessions.context(video_id=video_id, **fields)
            self.set_active(bool(video_id or fields.get("trip_id")))

    def _configure_schedule(self, now):
        face_present = self._face is not None
        idle = not face_present and now - self._no_face_since >= self.config.idle_after_seconds
        fps = self.config.detection_fps if face_present and self.active else self.config.no_face_fps
        if idle:
            fps = self.config.idle_detection_fps
        detection_interval = 1 / fps
        if self.detection_error:
            detection_interval = max(detection_interval, self.config.recovery_seconds)
        state = self.sessions.snapshot(now)["identity_status"]
        candidate = state == "candidate" or self.sessions.revalidation_pending or self._force_recheck
        interval = self.config.candidate_interval if candidate else (
            self.config.recheck_seconds if state == "verified" else self.config.recognition_interval)
        if not self.active and state == "verified" and not candidate:
            interval = max(interval, 10)
        enabled = self.config.enabled
        self.scheduler.configure("detection", enabled=enabled, priority=100, interval=detection_interval)
        self.scheduler.configure("recognition", enabled=enabled and face_present and self.config.recognition_enabled and self.config.threshold is not None,
                                 priority=80, interval=interval)
        self.scheduler.configure("landmarks", enabled=enabled and face_present and self.config.landmarks_enabled and (self.active or self.config.landmarks_when_idle),
                                 priority=50, interval=self.config.recovery_seconds if getattr(self.landmarker, "error", None) else 1 / self.config.landmark_fps)

    def sample_interval(self):
        # Producer cadence only; exact due times remain in the inference worker.
        return min((task.target_interval for task in self.scheduler.tasks.values() if task.enabled), default=1)

    @staticmethod
    def _same_face(a, b):
        ax, ay, aw, ah = a["bbox"]
        bx, by, bw, bh = b["bbox"]
        intersection = max(0, min(ax+aw,bx+bw)-max(ax,bx)) * max(0,min(ay+ah,by+bh)-max(ay,by))
        return intersection / max(1, aw*ah+bw*bh-intersection) >= .35

    def _timed(self, name, fn):
        began = time.perf_counter()
        try:
            return fn()
        finally:
            self.timings[name + "_ms"] = round((time.perf_counter()-began)*1000, 2)

    def _run_landmarks(self, frame, now):
        if self.scheduler.due("landmarks", now) and self.clock() - now < self.config.lost_timeout:
            self._landmark_at = now
            self.scheduler.mark("landmarks", now)
            try:
                self.measurements = self._timed("landmarks", lambda: self.landmarker.extract(frame, self._face, now))
            except Exception:
                self.measurements = empty_measurements()
        elif not self.scheduler.tasks["landmarks"].enabled:
            self.measurements = empty_measurements()
        self._update_fatigue(now)

    def _update_fatigue(self, now):
        with self._context_lock:
            session = self.sessions.snapshot(now)
            started, active = self.recording_started, self.active
        if started is not None and now < started:
            # An inference started before this recording must not seed its windows.
            self.fatigue.reset()
            return
        context = (session["video_id"], session["session_id"], session["driver_id"])
        _, event = self.fatigue.update(self.measurements, self.clock(), context, active)
        if event and session["video_id"] and started is not None and now >= started:
            self.fatigue_events.append({
                **event, "event_id": "fatigue_" + uuid4().hex,
                "video_id": session["video_id"], "trip_id": session["trip_id"],
                "vehicle_id": session["vehicle_id"], "driver_id": session["driver_id"],
                "driver_session_id": session["session_id"],
                "driver_identity_status": "verified" if session["identity_status"] == "verified" else "unknown",
                "timestamp": datetime.fromtimestamp(time.time() - max(0, self.clock()-now), timezone.utc).isoformat(),
                "video_offset_seconds": round(now - started, 3),
            })

    def process_frame(self, frame, now=None):
        now = self.clock() if now is None else now
        if self._fatigue_last_frame is not None and now - self._fatigue_last_frame >= self.config.lost_timeout:
            self.fatigue.reset()
        self._fatigue_last_frame = now
        if not self.config.enabled:
            self.sessions.invalidate()
            self.fatigue.reset()
            return self.result()
        if frame is None:
            self.sessions.observe(False, now)
            if self._face is not None:
                self._no_face_since = now
            self._face = None
            self.measurements = empty_measurements()
            self._update_fatigue(now)
            self._configure_schedule(now)
            return self.result()
        self._configure_schedule(now)
        fresh_detection = False
        if self.scheduler.due("detection", now):
            fresh_detection = True
            self._detection_at = now
            self.scheduler.mark("detection", now)
            try:
                face = self._timed("detection", lambda: self.detector.detect(frame))
                visible = face["detected"] and not face.get("ambiguous", False)
                if visible and self._face and not self._same_face(self._face, face):
                    self.fatigue.reset()
                    if self.sessions.snapshot(now)["identity_status"] != "verified":
                        self.sessions.invalidate("detecting")
                    self._force_recheck = True
                    self.measurements = empty_measurements()
                if not visible and self._face is not None:
                    self._no_face_since = now
                self._face = face if visible else None
                self.sessions.observe(visible, now)
                self.detection_error = getattr(self.detector, "error", None)
                if self.detection_error:
                    self.sessions.invalidate()
            except Exception:
                self._face = None
                self.sessions.observe(False, now)
                self.detection_error = "Driver face detection unavailable."
                self.sessions.invalidate()
        self._configure_schedule(now)
        if self._face is None:
            self.measurements = empty_measurements()
            if now - self._no_face_since >= self.config.lost_timeout:
                self.fatigue.reset()
            self._update_fatigue(now)
            return self.result()
        if not self.config.recognition_enabled or self.config.threshold is None:
            self.sessions.invalidate()
            self.error = "Recognition disabled." if not self.config.recognition_enabled else "Recognition requires a calibrated threshold."
            self._run_landmarks(frame, now)
            return self.result()
        verified = self.sessions.snapshot(now)["identity_status"] == "verified"
        if fresh_detection and self.scheduler.due("recognition", now):
            self._recognition_at = now
            self.scheduler.mark("recognition", now)
            from models.driver_identity import utc_now
            self.last_identity_check = utc_now()
            try:
                profiles = self.index.profiles()
                # Refresh can remove a driver without waiting for mismatch votes.
                current_id = self.sessions.snapshot(now)["driver_id"]
                if current_id and not any(p.get("driver_id") == current_id for p in profiles):
                    self.sessions.invalidate()
                match = self._timed("recognition", lambda: self.recognizer.recognize(frame, self._face, profiles))
                # A slow database/model must never certify a stale captured face.
                if self.clock() - now >= self.config.lost_timeout:
                    match = None
                self.sessions.match(match, now)
                self._force_recheck = False
                self.error = None
                if match and self.sessions.snapshot()["identity_status"] == "verified" and not verified:
                    try:
                        self.store.mark_recognized(match["driver_id"])
                    except Exception:
                        pass
            except Exception:
                self.sessions.invalidate()
                self.error = "Driver recognition or private database unavailable."
        # Detection (100), recognition (80), then landmarks (50).
        self._run_landmarks(frame, now)
        self._configure_schedule(now)
        return self.result()

    def result(self):
        session = self.sessions.snapshot()
        measurements = self.measurements if session["identity_status"] != "lost" else empty_measurements()
        return {"face_detected": self._face is not None and session["identity_status"] != "lost",
                "identity_status": session["identity_status"], "driver_id": session["driver_id"],
                "driver_display_name": session["driver_display_name"], "identity_confidence": session["recognition_confidence"],
                "landmarks_available": bool(measurements["face_visible"]), "fatigue_signals": measurements,
                "fatigue": dict(self.fatigue.state),
                "last_identity_check": self.last_identity_check, "error": self.detection_error or self.error,
                "landmark_error": getattr(self.landmarker, "error", None), "timings": dict(self.timings),
                "scheduler": self.scheduler.snapshot()}
