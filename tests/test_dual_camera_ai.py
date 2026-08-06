import unittest

import numpy as np

from core.dual_camera import CameraConfig, DualCameraManager, FRONT


class DummyPipeline:
    def process(self, frame, frame_number, camera_fps, media_timestamp_seconds=None):
        return frame, {
            "frame_number": frame_number,
            "camera_role": FRONT,
            "objects": [],
            "movement_detected": False,
        }


class DualCameraAIFeaturesTests(unittest.TestCase):
    def test_dual_camera_manager_processes_ai_frames_with_pipeline(self):
        manager = DualCameraManager([CameraConfig(index=0, role=FRONT, enabled=True)])
        manager.pipeline = DummyPipeline()

        frame = np.zeros((8, 8, 3), dtype=np.uint8)
        annotated, event = manager._handle_ai_frame(FRONT, frame, 3, 12.0)

        self.assertIsNotNone(annotated)
        self.assertEqual(event["frame_number"], 3)
        self.assertEqual(manager.latest_event["camera_role"], FRONT)
        self.assertEqual(manager.ai_metrics["processed_frames"], 1)


if __name__ == "__main__":
    unittest.main()
