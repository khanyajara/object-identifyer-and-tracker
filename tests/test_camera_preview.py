import unittest
import time
import numpy as np
from unittest.mock import Mock, patch

from core.dual_camera import CameraChannel, CameraConfig, DualCameraManager


class PreviewLifecycleTests(unittest.TestCase):
    def test_both_preview_preserves_separate_frames_and_labels_events(self):
        manager = DualCameraManager([CameraConfig(0), CameraConfig(1, role="rear")])
        front = np.full((480,640,3), 30, dtype=np.uint8)
        rear = np.full((480,640,3), 190, dtype=np.uint8)
        manager._preview_results = {
            "front": (front, dict(camera_role="front",objects=[dict(label="car")],vehicle_count=1),time.monotonic()),
            "rear": (rear, dict(camera_role="rear",objects=[dict(label="person")],people_count=1),time.monotonic()),
        }
        frame,event = manager.detection_preview("both")
        self.assertEqual(frame.shape, (480,1286,3))
        self.assertEqual(int(frame[300,300,0]),30)
        self.assertEqual(int(frame[300,946,0]),190)
        self.assertEqual([item["camera_role"] for item in event["objects"]],["front","rear"])
        self.assertEqual((event["vehicle_count"],event["people_count"]),(1,1))

    def test_both_preview_shows_placeholder_for_missing_camera(self):
        manager = DualCameraManager([CameraConfig(0)])
        frame,event = manager.detection_preview("both")
        self.assertEqual(frame.shape[0],480)
        self.assertEqual(event["camera_role"],"both")
        self.assertEqual(event["objects"],[])

    def test_preview_allocates_no_writer_or_video_id(self):
        channel = CameraChannel(CameraConfig(0))
        manager = DualCameraManager([])
        manager.channels = {"front": channel}
        with patch.object(manager, "open"), patch.object(channel, "open", return_value=True), patch("core.dual_camera.threading.Thread") as thread, patch("core.dual_camera.open_video_writer") as writer:
            thread.return_value.is_alive.return_value = True
            self.assertTrue(manager.start_preview())
            self.assertFalse(manager.is_recording)
            self.assertIsNone(channel.video_id)
            writer.assert_not_called()
            self.assertTrue(channel.preview_ai_enabled)

    def test_start_stop_recording_reuses_preview_thread(self):
        channel = CameraChannel(CameraConfig(0))
        channel.preview_ai_enabled = True
        thread = Mock()
        thread.is_alive.return_value = True
        channel._thread = thread
        channel._capture = Mock()
        writer = Mock()
        with patch("core.dual_camera.open_video_writer", return_value=(writer,"VP80")), patch("core.dual_camera.threading.Thread") as factory:
            channel.start("preview-test.webm", "test")
            self.assertTrue(channel.is_recording)
            channel.stop_recording()
            self.assertFalse(channel.is_recording)
            self.assertTrue(channel.is_live)
            self.assertFalse(channel._stop_event.is_set())
            factory.assert_not_called()
            channel._capture.release.assert_not_called()
            writer.release.assert_called_once()

    def test_close_preview_does_not_interrupt_recording(self):
        manager = DualCameraManager([])
        manager._recording = True
        with self.assertRaises(RuntimeError):
            manager.close_preview()

    def test_close_preview_releases_channels_and_clears_overlays(self):
        manager = DualCameraManager([])
        channel = Mock(preview_ai_enabled=True)
        manager.channels = {"front": channel}
        manager._preview_results = {"front": "old"}
        manager.close_preview()
        self.assertFalse(channel.preview_ai_enabled)
        channel.release.assert_called_once()
        self.assertEqual(manager._preview_results, {})
