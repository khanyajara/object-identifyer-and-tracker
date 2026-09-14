import unittest
from unittest.mock import Mock, patch

import numpy as np

from core.camera_sources import parse_devices
from core.dual_camera import CameraConfig, DualCameraManager, camera_configs_from_settings


class CameraSourceTests(unittest.TestCase):
    @patch("core.dual_camera.droidcam_index", return_value=0)
    @patch("core.dual_camera.windows_camera_devices", return_value=[(0, "DroidCam Video", True), (1, "PC Camera", False)])
    def test_explicit_computer_rear_is_not_disabled_by_format_listing(self, *_):
        front, rear = camera_configs_from_settings({"rear_camera_source": "computer", "dual_camera_enabled": True})
        self.assertEqual((front.index, rear.index), (0, 1))
        self.assertTrue(rear.enabled and rear.ai_enabled)

    @patch("core.dual_camera.droidcam_index", return_value=0)
    @patch("core.dual_camera.windows_camera_devices", return_value=[(0, "DroidCam Video", True)])
    def test_missing_computer_camera_does_not_assign_droidcam_to_driver(self, *_):
        front, rear = camera_configs_from_settings({"rear_camera_source": "computer", "rear_camera_index": 0})
        self.assertTrue(front.enabled)
        self.assertFalse(rear.enabled)

    def test_preview_uses_annotated_frames_from_selected_camera(self):
        from core.annotation import annotate_frame
        manager = DualCameraManager([CameraConfig(0), CameraConfig(1, role="rear")])
        raw = np.zeros((240, 320, 3), dtype=np.uint8)
        event = dict(objects=[dict(label="person", confidence=90, box=dict(x=50,y=80,width=100,height=100))], plates=[])
        annotated = annotate_frame(raw.copy(), event["objects"], [], False, 10)
        manager.pipeline = Mock()
        manager.pipeline.process.return_value = (annotated, event)
        manager._handle_ai_frame("rear", raw, 1, 1.0)
        visible, result = manager.detection_preview("rear")
        self.assertTrue(np.array_equal(visible, annotated))
        self.assertFalse(np.array_equal(visible, raw))
        self.assertEqual(result["camera_role"], "rear")
        _, front_result = manager.detection_preview("front")
        self.assertEqual(front_result["objects"], [])
        visible[:] = 0
        self.assertTrue(manager.detection_preview("rear")[0].any())

    def test_preview_discards_expired_detections(self):
        manager = DualCameraManager([CameraConfig(0)])
        frame = np.zeros((8,8,3), dtype=np.uint8)
        manager._preview_results["front"] = (frame, {"objects": [{"label": "person"}]}, -100)
        _, event = manager.detection_preview("front")
        self.assertEqual(event["objects"], [])

    def test_enumeration_preserves_video_ordinals_and_excludes_audio(self):
        text = '[dshow] "PC Camera" (none)\n[dshow] "DroidCam Video" (video)\n[dshow] "Mic" (audio)'
        self.assertEqual(parse_devices(text), [(0, "PC Camera", False), (1, "DroidCam Video", True)])

    @patch("core.dual_camera.droidcam_index", return_value=2)
    @patch("core.dual_camera.windows_camera_devices", return_value=[(2, "DroidCam Video", True)])
    def test_single_source_uses_droidcam_without_inventing_rear(self, *_):
        front, rear = camera_configs_from_settings({"dual_camera_enabled": True})
        self.assertEqual(front.index, 2)
        self.assertEqual(front.label, "DroidCam")
        self.assertTrue(front.ai_enabled)
        self.assertFalse(rear.enabled)

    @patch("core.dual_camera.windows_camera_devices", return_value=[(0, "Front", True), (1, "Rear", True)])
    def test_two_sources_keep_rear_object_detection(self, *_):
        front, rear = camera_configs_from_settings({"dual_camera_enabled": True})
        self.assertTrue(front.ai_enabled and rear.ai_enabled and rear.enabled)

    def test_rear_event_cannot_be_mislabeled_by_shared_pipeline(self):
        manager = DualCameraManager([CameraConfig(1, role="rear")])
        frame = np.zeros((8, 8, 3), dtype=np.uint8)
        manager.pipeline = Mock()
        manager.pipeline.process.return_value = (frame, {"camera_role": "front", "objects": [{"label": "person"}]})
        _, event = manager._handle_ai_frame("rear", frame, 1, 1.0)
        self.assertEqual(event["camera_role"], "rear")
        self.assertEqual(event["objects"][0]["label"], "person")

    @patch("core.dual_camera.time.sleep")
    @patch("core.dual_camera.droidcam_index", return_value=2)
    @patch("core.dual_camera.CameraChannel")
    def test_failed_second_camera_selects_droidcam(self, channel_class, *_):
        manager = DualCameraManager([])
        front, rear = Mock(), Mock()
        front.config = CameraConfig(0)
        rear.config = CameraConfig(1, role="rear")
        front.open.return_value = True
        rear.open.return_value = False
        manager.channels = {"front": front, "rear": rear}
        manager.droidcam_fallback_enabled = True
        fallback = channel_class.return_value
        fallback.open.return_value = True
        self.assertTrue(manager.open()["front"])
        self.assertIs(manager.channels["front"], fallback)
        front.release.assert_called_once()

    @patch("core.dual_camera.time.sleep")
    @patch("core.dual_camera.droidcam_index", return_value=2)
    @patch("core.dual_camera.CameraChannel")
    def test_unavailable_droidcam_preserves_working_front(self, channel_class, *_):
        manager = DualCameraManager([])
        front = Mock()
        front.config = CameraConfig(0)
        front.open.return_value = True
        manager.channels = {"front": front}
        manager.droidcam_fallback_enabled = True
        channel_class.return_value.open.return_value = False
        self.assertTrue(manager.open()["front"])
        self.assertIs(manager.channels["front"], front)
        front.release.assert_not_called()

    @patch("core.dual_camera.time.sleep")
    @patch("core.dual_camera.droidcam_index", return_value=2)
    @patch("core.dual_camera.CameraChannel")
    def test_droidcam_already_used_by_rear_is_not_opened_twice(self, channel_class, *_):
        manager = DualCameraManager([])
        front, rear = Mock(), Mock()
        front.config, rear.config = CameraConfig(0), CameraConfig(2, role="rear")
        front.open.return_value, rear.open.return_value = False, True
        manager.channels = {"front": front, "rear": rear}
        manager.droidcam_fallback_enabled = True
        manager.open()
        channel_class.assert_not_called()
