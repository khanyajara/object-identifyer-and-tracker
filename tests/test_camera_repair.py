import os
import sys
import time
import unittest
from unittest.mock import Mock, patch

import numpy as np

from core.browser_camera import BrowserCameraChannel, BrowserFrameSource
from core.capture_mode import browser_capture_enabled
from core.dual_camera import CameraConfig, DualCameraManager
from core.frame_source import CameraSource, LocalOpenCVCameraSource
from core.lazy_vision_pipeline import LazyVisionPipeline


class MockCameraSource(BrowserFrameSource):
    """Camera-free source with exactly the production latest-frame semantics."""
    def feed(self, value=0):
        self.offer(np.full((8, 8, 3), value, np.uint8))


class CameraRepairTests(unittest.TestCase):
    def test_component_reruns_keep_one_video_only_widget_and_normalize_pyav(self):
        from services.browser_camera_ui import render_browser_cameras
        manager = DualCameraManager.from_settings({"camera_input_mode": "browser"})
        st, webrtc = Mock(), Mock()
        st.session_state = {}
        st.fragment.side_effect = lambda **kwargs: lambda function: function
        webrtc.VideoProcessorBase = object
        with patch.dict(sys.modules, {"streamlit": st, "streamlit_webrtc": webrtc}):
            render_browser_cameras(manager)
            first = webrtc.webrtc_streamer.call_args.kwargs
            render_browser_cameras(manager)
            second = webrtc.webrtc_streamer.call_args.kwargs
            self.assertEqual(first["key"], second["key"])
            self.assertFalse(first["media_stream_constraints"]["audio"])
            self.assertEqual(first["video_receiver_size"], 1)
            processor = second["video_processor_factory"]()
            frame = Mock()
            frame.to_ndarray.return_value = np.zeros((8, 8, 3), np.uint8)
            processor.recv(frame)
            frame.to_ndarray.assert_called_once_with(format="bgr24")
            self.assertTrue(manager.channel("rear").browser_source.ready)
            processor.on_ended()
            self.assertFalse(manager.channel("rear").browser_source.ready)
        manager.release()

    def test_source_contract_and_rgb_normalization(self):
        source = MockCameraSource(8, 8, 15)
        self.assertIsInstance(source, CameraSource)
        self.assertIsInstance(LocalOpenCVCameraSource(Mock()), CameraSource)
        rgb = np.full((8, 8, 3), [255, 10, 0], np.uint8)
        source.offer(rgb, color_format="rgb")
        rgb[:] = 0
        ok, bgr = source.read()
        self.assertTrue(ok)
        self.assertEqual(bgr[0, 0].tolist(), [0, 10, 255])
        for invalid in (None, np.zeros((8, 8)), np.zeros((8, 8, 3), np.float32)):
            with self.assertRaises(ValueError):
                source.offer(invalid)

    def test_health_timeout_stale_recovery_and_bounded_memory(self):
        with patch("core.browser_camera.time.monotonic", return_value=100) as clock:
            source = MockCameraSource(8, 8, 15)
            self.assertEqual(source.health()["status"], "camera_permission_required")
            clock.return_value = 121
            self.assertEqual(source.health()["status"], "camera_error")
            for value in range(1000):
                source.feed(value % 256)
            self.assertEqual(source.health()["frames_dropped"], 999)
            self.assertEqual(len(source._arrival_times), 60)
            self.assertEqual(source.read()[1][0, 0, 0], 999 % 256)
            self.assertEqual(source.health()["frames_processed"], 1)
            clock.return_value = 124
            self.assertEqual(source.health()["status"], "camera_stale")
            clock.return_value = 127
            self.assertEqual(source.health()["status"], "camera_reconnecting")
            source.last_error = "conversion error"
            source.feed()
            self.assertEqual(source.health()["status"], "camera_active")
            self.assertIsNone(source.health()["last_error"])

    def test_backend_environment_precedence_and_no_local_camera_on_cloud(self):
        with patch.dict(os.environ, {"ROADWATCH_CAMERA_BACKEND": "browser"}), patch("core.dual_camera.cv2.VideoCapture") as capture:
            self.assertTrue(browser_capture_enabled({"camera_input_mode": "local"}))
            manager = DualCameraManager.from_settings()
            manager.start_preview()
            manager.release()
            capture.assert_not_called()
        with patch.dict(os.environ, {"ROADWATCH_CAMERA_BACKEND": "opencv"}):
            self.assertFalse(browser_capture_enabled())

    def test_stalled_source_recovery_does_not_close_current_peer(self):
        channel = BrowserCameraChannel(CameraConfig(index=0, width=8, height=8))
        source = channel.browser_source
        source.offer(np.zeros((8, 8, 3), np.uint8))
        channel._capture = source  # completed/stalled worker's handle
        try:
            self.assertTrue(channel.ensure_capture())
            self.assertFalse(source.closed)
            worker = channel._thread
            for _ in range(20):
                self.assertTrue(channel.ensure_capture())
                self.assertIs(channel._thread, worker)
        finally:
            channel.release()
        self.assertFalse(channel.is_live)

    def test_live_peer_replacement_stops_old_worker(self):
        channel = BrowserCameraChannel(CameraConfig(index=0, width=8, height=8))
        old = channel.browser_source
        old.offer(np.zeros((8, 8, 3), np.uint8))
        channel.ensure_capture()
        worker = channel._thread
        try:
            new = channel.new_connection()
            self.assertFalse(worker.is_alive())
            new.offer(np.ones((8, 8, 3), np.uint8))
            old.release()
            self.assertTrue(channel.ensure_capture())
            self.assertFalse(new.closed)
        finally:
            channel.release()

    def test_driver_consumes_same_channel_once_per_session(self):
        manager = DualCameraManager.from_settings({"camera_input_mode": "browser"})
        channel = manager.channel("rear")
        channel.browser_source.offer(np.zeros((8, 8, 3), np.uint8))
        with patch("services.driver_monitoring.runtime.DriverMonitoringRuntime") as factory:
            runtime = factory.return_value
            runtime._closed = False
            manager.ensure_browser_driver()
            manager.ensure_browser_driver()
            factory.assert_called_once()
            runtime.start.assert_called_once_with(source=channel.get_latest)
        manager.release()

    def test_local_legacy_dedicated_setting_reuses_configured_rig(self):
        from services.driver_monitoring.config import MonitoringConfig
        from services.driver_monitoring.runtime import DriverMonitoringRuntime
        from services.driver_monitoring import runtime as module
        service = DriverMonitoringRuntime(MonitoringConfig())
        settings = {"driver_camera_source": "dedicated", "driver_camera_index": 9,
                    "dual_camera_enabled": True, "front_camera_index": 0, "rear_camera_index": 1,
                    "roadwatch_camera_backend": "opencv", "droidcam_fallback_enabled": False}
        with patch.object(module, "_runtime", service), patch("core.dual_camera.cv2.VideoCapture") as capture:
            service.prepare(settings)
            manager = DualCameraManager.from_settings(settings)
            self.assertIs(manager.channel("rear"), service.channel)
            self.assertNotEqual(service.channel.config.index, 9)
            capture.assert_not_called()

    def test_same_local_index_has_only_one_owner(self):
        from core.dual_camera import camera_configs_from_settings
        configs = camera_configs_from_settings({"front_camera_index": 0, "rear_camera_index": 0,
            "dual_camera_enabled": True, "droidcam_fallback_enabled": False})
        self.assertEqual(sum(config.enabled for config in configs), 1)

    def test_driver_worker_consumes_injected_frame_and_exits_when_source_stales(self):
        from services.driver_monitoring.config import MonitoringConfig
        from services.driver_monitoring.runtime import DriverMonitoringRuntime
        config = MonitoringConfig(recovery_seconds=.01, lost_timeout=.05)
        runtime = DriverMonitoringRuntime(config)
        frame = np.zeros((8, 8, 3), np.uint8)
        runtime.service.process_frame = Mock(return_value=runtime.service.result())
        runtime.source_idle_timeout = .1
        stamp = time.time()
        try:
            runtime.start(source=lambda: (frame, 1, stamp))
            deadline = time.monotonic() + 2
            while runtime.running and time.monotonic() < deadline:
                time.sleep(.01)
            runtime.service.process_frame.assert_called_once()
            self.assertEqual(runtime.metrics["processed_frames"], 1)
            self.assertTrue(runtime._closed)
            self.assertIsNone(runtime.channel)  # no camera-owning fallback
        finally:
            runtime.stop()
        self.assertEqual(runtime.diagnostics()["background_threads"], 0)

    def test_mp4_metadata_retains_validated_playback_path(self):
        from services.dual_session_service import build_video_metadata
        metadata = build_video_metadata({}, {"video_path": "original.mp4",
            "compressed_processed_path": "compressed.mp4", "upload_video_path": "compressed.mp4",
            "processed_compression_status": "success"})
        self.assertEqual(metadata["video_format"], "mp4")
        self.assertEqual(metadata["compressed_processed_path"], "compressed.mp4")
        self.assertEqual(metadata["upload_video_path"], "compressed.mp4")

    def test_failed_yolo_initialization_never_blocks_capture_and_backs_off(self):
        settings = dict(model_name="missing", confidence=.5, yolo_image_size=320,
                        enable_tracking=True, enable_ocr=False, ocr_interval_seconds=5)
        pipeline = LazyVisionPipeline(settings)
        frame = np.zeros((8, 8, 3), np.uint8)
        with patch("core.vision_pipeline.VisionPipeline", side_effect=RuntimeError("model unavailable")) as factory:
            for _ in range(10):
                output, event = pipeline.process(frame, 1, 15)
                self.assertIs(output, frame)
                self.assertIn("model unavailable", event["error"])
            factory.assert_called_once()

    def test_both_ai_and_recorder_receive_shared_frames(self):
        manager = DualCameraManager.from_settings({"camera_input_mode": "browser"})
        manager.pipeline = Mock()
        manager.pipeline.process.side_effect = lambda frame, *a, **k: (frame, {"objects": []})
        channel = manager.channel("rear")
        channel.config.ai_sample_interval = 0
        writer = Mock()
        channel._writer = writer
        channel._recording = True
        channel.preview_ai_enabled = True
        try:
            channel.browser_source.offer(np.zeros((8, 8, 3), np.uint8))
            channel.ensure_capture()
            deadline = time.monotonic() + 2
            while channel._ai_queue.empty() and time.monotonic() < deadline:
                time.sleep(.01)
            manager.process_live_ai()
            writer.write.assert_called_once()
            manager.pipeline.process.assert_called_once()
            self.assertEqual(channel.frames_written, 1)
        finally:
            manager.release()


if __name__ == "__main__":
    unittest.main()
