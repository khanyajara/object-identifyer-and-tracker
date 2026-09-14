import unittest
from unittest.mock import Mock, patch

from services import dual_video_processing as processing


class CameraProcessingTests(unittest.TestCase):
    def test_processing_preserves_source_and_records_outcome(self):
        camera = {
            "role": "front", "video_id": "test", "video_path": "original.mp4",
            "true_fps": 12.0, "measured_fps": 20.0,
        }
        pipeline = object()
        events = [{"frame_number": 1, "camera_role": "front"}]
        for ok, error in [(True, None), (False, "No usable codec")]:
            with self.subTest(ok=ok), patch.object(
                processing, "_video_is_readable", return_value=True
            ), patch.object(
                processing, "processed_path_for", return_value="processed.webm"
            ), patch.object(
                processing, "_built_in_pass", return_value=(ok, events, error)
            ) as process:
                progress = Mock()
                result = processing.process_camera({}, camera, pipeline, progress)
                process.assert_called_once_with(
                    camera, pipeline, "processed.webm", 12.0, progress
                )
                self.assertEqual(result["video_path"], "original.mp4")
                self.assertNotIn("processing_status", camera)
                self.assertEqual(result["processing_error"], error)
                self.assertEqual(result["processed_video_path"], "processed.webm" if ok else None)
                if ok:
                    self.assertEqual(result["detections"], events)
                progress.assert_called_with("front", 1.0, "Done" if ok else "Failed")

    def test_unreadable_recording_skips_pipeline(self):
        with patch.object(processing, "_video_is_readable", return_value=False), patch.object(
            processing, "_built_in_pass"
        ) as process:
            result = processing.process_camera({}, {"video_path": "missing.mp4"})
        process.assert_not_called()
        self.assertEqual(result["processing_status"], "Skipped.")
        self.assertEqual(result["video_path"], "missing.mp4")
