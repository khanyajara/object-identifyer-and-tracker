"""Process-owned background monitoring, shared capture and a latest-only queue."""
import atexit
import copy
import logging
import os
import queue
import threading
import time
from concurrent.futures import Future
from .config import MonitoringConfig
from .driver_monitoring_service import DriverMonitoringService
from .face_enrollment_service import FaceEnrollmentService
from .face_landmark_service import empty_measurements

LOGGER = logging.getLogger("roadwatch.driver_monitoring")


class DriverMonitoringRuntime:
    def __init__(self, config):
        self.config = config
        self.service = DriverMonitoringService(config)
        self.enrollment = FaceEnrollmentService(self.service.detector, self.service.recognizer, self.service.store)
        self._thread = self._producer_thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._lifecycle_lock = threading.RLock()
        self._commands = queue.Queue(maxsize=4)
        self._frames = queue.Queue(maxsize=1)
        self._source = None
        self._generation = 0
        self._inference_generation = None
        self.channel = None
        self._result = self.service.result()
        self._enrollment_status = dict(self.enrollment.status)
        self._published_at = 0.0
        self._sample_interval = 1 / config.no_face_fps
        self._closed = False
        self._prepared = False
        self._last_transition = ("no_face", None)
        self._last_error = None
        self._fatigue_write_failed = False
        self._fatigue_retry_at = 0
        self.metrics = {"offered_frames": 0, "dropped_frames": 0, "processed_frames": 0,
                        "stale_frames": 0, "worker_recoveries": 0, "camera_retries": 0}
        self.message = "Driver monitoring unavailable."

    @property
    def running(self):
        return bool(self._thread and self._thread.is_alive() and not self._stop.is_set())

    def prepare(self, settings, existing_manager=None):
        """Select a server-configured source once, without opening it on the UI thread."""
        with self._lifecycle_lock:
            if self._prepared or not self.config.enabled or not self.config.background:
                return
            self._prepared = True
            from core.dual_camera import CameraChannel, CameraConfig, camera_configs_from_settings
            source_kind = settings.get("driver_camera_source") or os.getenv("DRIVER_CAMERA_SOURCE", "dedicated")
            configs = camera_configs_from_settings(settings)
            front = next(c for c in configs if c.role == "front")
            rear = next(c for c in configs if c.role == "rear")
            if source_kind == "rear":
                if not rear.enabled or rear.index == front.index:
                    return
                camera_config = rear
            else:
                try:
                    index = int(settings.get("driver_camera_index", os.getenv("DRIVER_CAMERA_INDEX", "")))
                except (ValueError, TypeError):
                    return
                excluded = {front.index, int(settings.get("camera_index", front.index))}
                if rear.enabled:
                    excluded.add(rear.index)
                if index < 0 or index in excluded:
                    return
                camera_config = CameraConfig(index=index, role="driver", width=self.config.width,
                    height=self.config.height, target_fps=15, ai_enabled=False)
            existing_channel = None
            dual = getattr(existing_manager, "manager", existing_manager)
            if source_kind == "rear" and dual is not None and hasattr(dual, "channel"):
                candidate = dual.channel("rear")
                if candidate is not None and candidate.config.index == camera_config.index:
                    existing_channel = candidate
            self.channel = existing_channel or CameraChannel(camera_config)
            self.channel.keep_capture_alive = True
            self._source = self.channel.get_latest

    def start(self, source=None, excluded_indices=()):
        """Idempotent internal startup; source injection supports camera-free tests."""
        with self._lifecycle_lock:
            if self._closed or not self.config.enabled or not self.config.background:
                return
            if source is not None and source != self._source:
                self._source = source
                self._generation += 1
                self.service.sessions.invalidate()
            if self._source is None:
                try:
                    index = int(os.getenv("DRIVER_CAMERA_INDEX", ""))
                except ValueError:
                    return
                if index in excluded_indices or index < 0:
                    return
                self.prepare({"camera_index": min(excluded_indices, default=0), "dual_camera_enabled": False})
                if self._source is None:
                    return
            if self._thread and self._thread.is_alive():
                return
            if self._producer_thread and self._producer_thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="driver-monitoring", daemon=True)
            self._producer_thread = threading.Thread(target=self._produce, name="driver-frame-producer", daemon=True)
            self._thread.start()
            self._producer_thread.start()
            LOGGER.info("Driver monitoring initialized.")

    def _offer(self, item):
        try:
            self._frames.put_nowait(item)
        except queue.Full:
            try:
                self._frames.get_nowait()
                self._frames.task_done()
                self.metrics["dropped_frames"] += 1
            except queue.Empty:
                pass
            self._frames.put_nowait(item)
        self.metrics["offered_frames"] += 1

    def _produce(self):
        previous_token = None
        retry_at = 0.0
        failed = False
        try:
            while not self._stop.is_set():
                try:
                    if self.channel is not None and not self.channel.is_live:
                        if time.monotonic() < retry_at:
                            self._stop.wait(min(1, retry_at-time.monotonic()))
                            continue
                        self.metrics["camera_retries"] += 1
                        self.service.sessions.invalidate("lost")
                        if not self.channel.ensure_capture():
                            raise RuntimeError("camera unavailable")
                    source, generation = self._source, self._generation
                    frame, number, stamp = source()
                    age = max(0, time.time() - stamp)
                    if frame is None or age >= self.config.lost_timeout:
                        self.message = "Driver monitoring unavailable."
                        self._stop.wait(min(1, self.config.recovery_seconds))
                        continue
                    token = (generation, number, stamp)
                    if token != previous_token:
                        previous_token = token
                        captured_at = time.monotonic() - age
                        if frame.shape[1] > self.config.width or frame.shape[0] > self.config.height:
                            import cv2
                            frame = cv2.resize(frame, (self.config.width, self.config.height), interpolation=cv2.INTER_AREA)
                        self._offer((frame, captured_at, generation))
                        self.message = "Driver monitoring active."
                    if failed:
                        LOGGER.info("Driver camera recovered.")
                        failed = False
                    interval = .1 if self._enrollment_status["status"] == "capturing" else self._sample_interval
                    self._stop.wait(max(.02, min(1, interval)))
                except Exception:
                    if not failed:
                        LOGGER.warning("Driver camera unavailable; background recovery scheduled.")
                        failed = True
                    self.message = "Driver monitoring unavailable."
                    self.service.sessions.invalidate("lost")
                    retry_at = time.monotonic() + self.config.recovery_seconds
                    self._stop.wait(self.config.recovery_seconds)
        finally:
            if self.channel is not None:
                self.channel.release(force=True)

    def _commands_once(self):
        try:
            future, fn = self._commands.get_nowait()
        except queue.Empty:
            return
        try:
            if future.set_running_or_notify_cancel():
                future.set_result(fn())
        except Exception:
            future.set_exception(RuntimeError("Driver action unavailable. Check private storage and enrollment configuration."))
        finally:
            self._commands.task_done()

    def _publish(self, result):
        with self._lock:
            self._result = result
            self._enrollment_status = dict(self.enrollment.status)
            self._published_at = time.monotonic()
            self._sample_interval = self.service.sample_interval()
        transition = (result["identity_status"], result["driver_id"])
        if transition != self._last_transition:
            if transition[0] == "verified":
                LOGGER.info("Driver verified: %s.", transition[1])
            elif self._last_transition[0] == "verified":
                LOGGER.info("Driver identity lost.")
            self._last_transition = transition
        error = result.get("error") or result.get("landmark_error")
        if error != self._last_error:
            if error:
                LOGGER.warning("Driver monitoring capability unavailable.")
            self._last_error = error
        while self.service.fatigue_events and time.monotonic() >= self._fatigue_retry_at:
            event = self.service.fatigue_events.popleft()
            try:
                from .fatigue_event_store import FatigueEventStore
                from services.gps_service import GPSService
                try:
                    gps = GPSService().latest()
                except Exception:
                    gps = None
                FatigueEventStore().save(event, gps)
                self._fatigue_write_failed = False
            except Exception:
                # Preserve a bounded pending event for retry; never block recording.
                self.service.fatigue_events.appendleft(event)
                if not self._fatigue_write_failed:
                    LOGGER.warning("Fatigue event storage unavailable.")
                self._fatigue_write_failed = True
                self._fatigue_retry_at = time.monotonic() + self.config.database_retry_seconds
                break

    def _run(self):
        failed = False
        try:
            while not self._stop.is_set():
                try:
                    self._commands_once()
                    try:
                        item = self._frames.get(timeout=.25)
                    except queue.Empty:
                        self.service.sessions.snapshot()
                        self.enrollment.process(None, time.monotonic())
                        self._publish(self.service.result())
                        continue
                    try:
                        frame, captured_at, generation = item
                        if generation != self._generation or time.monotonic()-captured_at >= min(1, self.config.lost_timeout):
                            self.metrics["stale_frames"] += 1
                            continue
                        if generation != self._inference_generation:
                            self.service.fatigue.reset()
                            self._inference_generation = generation
                        if self.enrollment.status["status"] == "capturing":
                            self.service.fatigue.reset()
                            self.service.sessions.invalidate()
                            self.service._face = None
                            self.enrollment.process(frame, time.monotonic())
                            self.service.measurements = empty_measurements()
                            result = self.service.result()
                        else:
                            result = self.service.process_frame(frame, captured_at)
                        self.metrics["processed_frames"] += 1
                        self._publish(result)
                    finally:
                        self._frames.task_done()
                    if failed:
                        self.metrics["worker_recoveries"] += 1
                        LOGGER.info("Recognition worker recovered.")
                        failed = False
                except Exception:
                    if not failed:
                        LOGGER.error("Driver monitoring worker failed; controlled recovery scheduled.")
                        failed = True
                    self.service.sessions.invalidate("lost")
                    self.message = "Driver monitoring unavailable."
                    self._stop.wait(self.config.recovery_seconds)
        finally:
            self.service.sessions.invalidate("lost")
            self.enrollment.cancel()
            self.service.landmarker.close()
            self.service.index.invalidate()
            for pending in (self._frames, self._commands):
                while True:
                    try:
                        item = pending.get_nowait()
                        if pending is self._commands:
                            item[0].cancel()
                        pending.task_done()
                    except queue.Empty:
                        break

    def submit(self, fn):
        future = Future()
        if not self.running:
            future.set_exception(RuntimeError("Driver monitoring unavailable."))
            return future
        try:
            self._commands.put_nowait((future, fn))
        except queue.Full:
            future.set_exception(RuntimeError("Driver worker busy; wait for the current action."))
        return future

    def manage_enrollment(self, fn):
        def action():
            self.enrollment.cancel()
            self.service.sessions.invalidate()
            try:
                return fn()
            finally:
                self.service.index.invalidate()
        return self.submit(action)

    def snapshot(self):
        with self._lock:
            result = copy.deepcopy(self._result)
            result["enrollment"] = dict(self._enrollment_status)
        session = self.service.sessions.snapshot()
        result.update(identity_status=session["identity_status"], driver_id=session["driver_id"],
                      driver_display_name=session["driver_display_name"], identity_confidence=session["recognition_confidence"])
        if session["identity_status"] == "lost" or not self.running:
            result.update(face_detected=False, landmarks_available=False, fatigue_signals={})
        fatigue = result.get("fatigue", {})
        stamp = fatigue.get("sampled_at_monotonic")
        if not self.running or not self.service.active or stamp is None or time.monotonic() - stamp > self.service.fatigue.config.max_gap:
            result["fatigue"] = {"status": "unavailable", "severity": "none", "score": None}
        if not self.running:
            result.update(identity_status="unknown", driver_id=None, driver_display_name="Unknown Driver", identity_confidence=0.0)
        result["message"] = self.message
        return result

    def diagnostics(self):
        return {**self.metrics, "pending_frames": self._frames.qsize(), "frame_queue_capacity": self._frames.maxsize,
                "pending_fatigue_events": len(self.service.fatigue_events), "fatigue_storage_failed": self._fatigue_write_failed,
                "fatigue": self.snapshot().get("fatigue", {}), "inference_timings_ms": self.snapshot().get("timings", {}),
                "background_threads": sum(bool(t and t.is_alive()) for t in (self._thread, self._producer_thread)),
                "index_refreshes": self.service.index.refresh_count, "scheduler": self.snapshot().get("scheduler", {})}

    def stop(self, timeout=8):
        with self._lifecycle_lock:
            self._closed = True
            self._stop.set()
            self.service.sessions.invalidate("lost")
        deadline = time.monotonic() + timeout
        for thread in (self._producer_thread, self._thread):
            if thread and thread is not threading.current_thread():
                thread.join(timeout=max(0, deadline-time.monotonic()))
        self.message = "Driver monitoring stopped."
        if any(t and t.is_alive() for t in (self._producer_thread, self._thread)):
            LOGGER.warning("Driver shutdown is waiting for a native camera/model call.")


_runtime = None
_runtime_lock = threading.Lock()


def get_runtime():
    global _runtime
    with _runtime_lock:
        if _runtime is None:
            try:
                config = MonitoringConfig.from_env()
            except (ValueError, TypeError):
                config = MonitoringConfig(enabled=False)
                LOGGER.warning("Driver monitoring configuration unavailable.")
            _runtime = DriverMonitoringRuntime(config)
            atexit.register(_runtime.stop)
        return _runtime


def ensure_background(settings=None, existing_manager=None):
    try:
        runtime = get_runtime()
        runtime.prepare(settings or {}, existing_manager)
        if runtime._source is not None:
            runtime.start()
        return runtime
    except Exception:
        LOGGER.warning("Driver background initialization unavailable.")
        return None


def shared_driver_channel(config):
    runtime = _runtime
    if runtime is not None and runtime.channel is not None and not runtime._closed:
        channel = runtime.channel
        if config.enabled and config.role == channel.config.role and config.index == channel.config.index:
            return channel
    return None


def identity_metadata(record=None):
    if record is not None:
        return {key: record[key] for key in ("driver_id", "driver_identity_status", "driver_session_id") if key in record}
    try:
        if _runtime is not None:
            data = _runtime.snapshot()
            session = _runtime.service.sessions.snapshot()
            return {"driver_id": data["driver_id"], "driver_identity_status": "verified" if data["identity_status"] == "verified" else "unknown",
                    "driver_session_id": session["session_id"]}
    except Exception:
        pass
    return {"driver_id": None, "driver_identity_status": "unknown", "driver_session_id": None}


def recording_context(video_id=None, **fields):
    try:
        if _runtime is not None:
            _runtime.service.recording_context(video_id, **fields)
    except Exception:
        LOGGER.warning("Driver recording context unavailable; recording continues.")


def bind_recording(manager, settings, gps=None):
    try:
        from services.vehicle_profile_service import VehicleProfileService
        profile = VehicleProfileService().get_profile()
        recording_context(manager.record.get("video_id"), trip_id=(gps or {}).get("trip_id"),
                          vehicle_id=settings.get("vehicle_id") or profile.get("registration_plate") or None)
    except Exception:
        pass
