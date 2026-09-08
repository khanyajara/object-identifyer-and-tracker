import threading
import time
import unittest
from unittest.mock import Mock, patch
import numpy as np
from core.dual_camera import CameraChannel, CameraConfig, DualCameraManager
from services.driver_monitoring.config import MonitoringConfig
from services.driver_monitoring.embedding_index import DriverEmbeddingIndex
from services.driver_monitoring.inference_scheduler import InferenceScheduler
from services.driver_monitoring.runtime import DriverMonitoringRuntime


class IndexTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.store = Mock(revision=0)
        self.store.recognition_profiles.return_value = [{"driver_id": "A"}]
        self.index = DriverEmbeddingIndex(self.store, ttl=60, retry_seconds=10, clock=lambda: self.now)

    def test_recognition_uses_memory_until_ttl_or_revision_changes(self):
        for self.now in range(60):
            self.assertEqual(self.index.profiles()[0]["driver_id"], "A")
        self.assertEqual(self.store.recognition_profiles.call_count, 1)
        self.now = 60
        self.index.profiles()
        self.store.revision += 1
        self.store.recognition_profiles.return_value = []
        self.assertEqual(self.index.profiles(), [])
        self.assertEqual(self.store.recognition_profiles.call_count, 3)

    def test_failed_refresh_discards_cache_and_backs_off(self):
        self.index.profiles()
        self.now = 61
        self.store.recognition_profiles.side_effect = RuntimeError("private detail")
        for _ in range(20):
            with self.assertRaisesRegex(RuntimeError, "^Private recognition index unavailable.$"):
                self.index.profiles()
        self.assertEqual(self.store.recognition_profiles.call_count, 2)
        self.now = 72
        self.store.recognition_profiles.side_effect = None
        self.index.profiles()
        self.assertEqual(self.store.recognition_profiles.call_count, 3)

    def test_explicit_refresh(self):
        self.index.profiles()
        self.index.invalidate()
        self.index.profiles()
        self.assertEqual(self.store.recognition_profiles.call_count, 2)


class SchedulerTests(unittest.TestCase):
    def test_priority_enable_and_due_times_are_independent(self):
        scheduler = InferenceScheduler()
        scheduler.configure("future_model", enabled=False, priority=5, interval=10)
        scheduler.configure("face", enabled=True, priority=100, interval=.25)
        scheduler.configure("recognition", enabled=True, priority=80, interval=7)
        self.assertEqual(scheduler.ready(0), ["face", "recognition"])
        scheduler.mark("face", 0)
        scheduler.mark("recognition", 0)
        self.assertEqual(scheduler.ready(1), ["face"])
        self.assertFalse(scheduler.due("future_model", 100))

    def test_no_face_and_idle_reduce_work_and_disable_landmarks(self):
        from services.driver_monitoring.driver_monitoring_service import DriverMonitoringService
        now = [0]
        detector = Mock(error=None)
        detector.detect.return_value = {"detected": False}
        landmarks = Mock(error=None)
        store = Mock()
        service = DriverMonitoringService(MonitoringConfig(), detector=detector, landmarker=landmarks, store=store, clock=lambda: now[0])
        frame = np.zeros((8,8,3), np.uint8)
        for i in range(100):
            now[0] = i / 100
            service.process_frame(frame)
        self.assertEqual(detector.detect.call_count, 4)
        now[0] = 31
        service.process_frame(frame)
        self.assertEqual(service.scheduler.tasks["detection"].target_interval, 1)
        landmarks.extract.assert_not_called()
        store.recognition_profiles.assert_not_called()


class BackgroundRuntimeTests(unittest.TestCase):
    def test_queue_drops_old_pending_frames(self):
        runtime = DriverMonitoringRuntime(MonitoringConfig())
        for number in range(100):
            runtime._offer((number, time.monotonic(), 0))
        self.assertEqual(runtime._frames.qsize(), 1)
        self.assertEqual(runtime._frames.get_nowait()[0], 99)
        self.assertEqual(runtime.metrics["dropped_frames"], 99)

    def test_repeated_start_keeps_threads_and_verified_session(self):
        runtime = DriverMonitoringRuntime(MonitoringConfig(threshold=None))
        source = Mock(return_value=(None, 0, 0))
        runtime.start(source=source)
        try:
            runtime.service.sessions.session.identity_status = "verified"
            runtime.service.sessions.session.driver_id = "A"
            threads = runtime._thread, runtime._producer_thread
            session_id = runtime.service.sessions.session.session_id
            for _ in range(100):
                runtime.start(source=source)
            self.assertEqual(threads, (runtime._thread, runtime._producer_thread))
            self.assertEqual(runtime.service.sessions.session.session_id, session_id)
            self.assertEqual(runtime.service.sessions.session.driver_id, "A")
        finally:
            runtime.stop()
        self.assertFalse(any(t.is_alive() for t in threads))

    def test_worker_recovers_without_recreating_models(self):
        runtime = DriverMonitoringRuntime(MonitoringConfig(recovery_seconds=.02))
        frame = np.zeros((8,8,3), np.uint8)
        calls = [0]
        result = runtime.service.result()
        def process(*args):
            calls[0] += 1
            if calls[0] == 1:
                raise RuntimeError("test model failure")
            return result
        runtime.service.process_frame = process
        models = runtime.service.detector, runtime.service.recognizer, runtime.service.landmarker
        def source():
            return frame, 1, time.time()
        runtime.start(source=source)
        deadline = time.monotonic() + 2
        try:
            while runtime.metrics["worker_recoveries"] == 0 and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertEqual(runtime.metrics["worker_recoveries"], 1)
            self.assertEqual(models, (runtime.service.detector, runtime.service.recognizer, runtime.service.landmarker))
        finally:
            runtime.stop()

    def test_startup_reuses_existing_rear_channel_without_opening_on_render(self):
        from services.driver_monitoring import runtime as module
        service = DriverMonitoringRuntime(MonitoringConfig())
        settings = {"driver_camera_source": "rear", "dual_camera_enabled": True, "front_camera_index": 0, "rear_camera_index": 1}
        with patch.object(module, "_runtime", service), patch.object(CameraChannel, "open") as opened:
            service.prepare(settings)
            manager = DualCameraManager.from_settings(settings)
            self.assertIs(manager.channel("rear"), service.channel)
            self.assertIsNot(manager.channel("front"), service.channel)
            opened.assert_not_called()

    def test_camera_failure_retries_in_background(self):
        runtime = DriverMonitoringRuntime(MonitoringConfig(recovery_seconds=.02))
        channel = Mock(is_live=False)
        channel.get_latest.return_value = (None, 0, 0)
        attempts = [0]
        def open_camera():
            attempts[0] += 1
            if attempts[0] < 2:
                return False
            channel.is_live = True
            return True
        channel.ensure_capture.side_effect = open_camera
        runtime.channel = channel
        runtime.start(source=channel.get_latest)
        try:
            deadline = time.monotonic() + 1
            while attempts[0] < 2 and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertEqual(attempts[0], 2)
        finally:
            runtime.stop()
        channel.release.assert_called_once_with(force=True)

    def test_existing_recorder_capture_is_adopted_at_startup(self):
        service = DriverMonitoringRuntime(MonitoringConfig())
        settings = {"driver_camera_source": "rear", "dual_camera_enabled": True, "rear_camera_index": 1, "front_camera_index": 0}
        existing = CameraChannel(CameraConfig(1, role="rear"))
        manager = Mock()
        manager.manager.channel.return_value = existing
        with patch("core.dual_camera.CameraChannel") as factory:
            service.prepare(settings, manager)
            self.assertIs(service.channel, existing)
            factory.assert_not_called()

    def test_recording_attaches_writer_without_new_capture_thread(self):
        channel = CameraChannel(CameraConfig(1, role="rear"))
        channel.keep_capture_alive = True
        capture_thread = Mock()
        capture_thread.is_alive.return_value = True
        channel._thread = capture_thread
        channel._capture = Mock()
        writer = Mock()
        with patch("core.dual_camera.open_video_writer", return_value=(writer, "VP80")), patch("core.dual_camera.threading.Thread") as thread_factory:
            self.assertTrue(channel.start("test.webm", "video"))
            channel.stop_recording()
            self.assertIs(channel._thread, capture_thread)
            self.assertFalse(channel._stop_event.is_set())
            thread_factory.assert_not_called()
            writer.release.assert_called_once()
            channel._capture.release.assert_not_called()

    def test_default_background_configuration_and_legacy_aliases(self):
        with patch.dict("os.environ", {}, clear=True):
            config = MonitoringConfig.from_env()
            self.assertTrue(config.enabled and config.background)
        with patch.dict("os.environ", {"FACE_RECOGNITION_INTERVAL_SECONDS": "3", "FACE_RECOGNITION_UNVERIFIED_INTERVAL_SECONDS": "2"}, clear=True):
            self.assertEqual(MonitoringConfig.from_env().recognition_interval, 2)
