import unittest
from unittest.mock import Mock

import numpy as np

from core.dual_camera import CameraConfig, DualCameraManager
from services.driver_monitoring.config import MonitoringConfig
from services.driver_monitoring.runtime import DriverMonitoringRuntime


class LiveAITests(unittest.TestCase):
    def test_app_refreshes_a_cached_manager_class_missing_the_switch(self):
        import streamlit_app as app
        from unittest.mock import patch
        current = app.dual_camera_module.DualCameraManager
        def reload_module(module):
            module.DualCameraManager = current
            return module
        with patch.object(app.dual_camera_module, "DualCameraManager", object), \
             patch("importlib.reload", side_effect=reload_module) as reload:
            self.assertIs(app.load_dual_camera_manager_class(), current)
        reload.assert_called_once_with(app.dual_camera_module)

    def test_dashboard_reports_each_camera_and_raw_preview_while_paused(self):
        from streamlit_app import DualCameraUIManager
        ui = DualCameraUIManager.__new__(DualCameraUIManager)
        ui.manager = DualCameraManager([CameraConfig(0), CameraConfig(1, role="rear")])
        ui.manager.status = Mock(return_value={
            "front": {"live_fps": 29.0, "status": "online"},
            "rear": {"live_fps": 18.0, "status": "online"},
        })
        ui.manager.set_live_ai_enabled(False)
        frame, event, metrics, error = ui.get_dashboard_state()
        self.assertIsNotNone(frame)
        self.assertEqual(event["objects"], [])
        self.assertEqual([row["fps"] for row in metrics["camera_sources"]], [29.0, 18.0])
        self.assertFalse(metrics["live_ai_enabled"])

    def test_pause_preserves_capture_and_recording(self):
        manager = DualCameraManager([CameraConfig(0)])
        channel = manager.channels["front"]
        channel.preview_ai_enabled = True
        channel._recording = True
        channel._capture = Mock()
        manager.latest_event = {"objects": [{"label": "car"}]}
        manager.set_live_ai_enabled(False)
        self.assertTrue(channel.is_recording)
        self.assertTrue(channel.preview_ai_enabled)
        channel._capture.release.assert_not_called()
        self.assertIsNone(manager.latest_event)
        self.assertFalse(channel.live_ai_enabled)
        manager.set_live_ai_enabled(True)
        self.assertTrue(channel.live_ai_enabled)

    def test_paused_preview_never_invokes_pipeline(self):
        manager = DualCameraManager([CameraConfig(0)])
        manager.pipeline = Mock()
        manager.set_live_ai_enabled(False)
        frame = np.zeros((4, 4, 3), dtype=np.uint8)
        returned, event = manager._handle_ai_frame("front", frame, 1, 0)
        self.assertIs(returned, frame)
        self.assertEqual(event["objects"], [])
        manager.pipeline.process.assert_not_called()

    def test_inflight_result_is_discarded_after_pause(self):
        manager = DualCameraManager([CameraConfig(0)])
        frame = np.zeros((4, 4, 3), dtype=np.uint8)
        def process(*args, **kwargs):
            manager.set_live_ai_enabled(False)
            return frame, {"objects": [{"label": "car"}]}
        manager.pipeline = Mock()
        manager.pipeline.process.side_effect = process
        manager._handle_ai_frame("front", frame, 1, 0)
        self.assertEqual(manager._preview_results, {})
        self.assertIsNone(manager.latest_event)

    def test_driver_pause_hides_stale_identity_and_fatigue(self):
        runtime = DriverMonitoringRuntime(MonitoringConfig())
        runtime.set_live_ai_enabled(False)
        snapshot = runtime.snapshot()
        self.assertEqual(snapshot["identity_status"], "unknown")
        self.assertEqual(snapshot["fatigue"]["status"], "paused")
        self.assertFalse(snapshot["face_detected"])
        generation = runtime._generation
        runtime.set_live_ai_enabled(False)
        self.assertEqual(runtime._generation, generation)
        runtime.set_live_ai_enabled(True)
        self.assertGreater(runtime._generation, generation)
