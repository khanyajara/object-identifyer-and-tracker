import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from core.detection_policy import detection_confidence, suppress_duplicates
from core.tracker import ObjectTracker


def detection(x=0, label="car", confidence=95):
    return dict(label=label, confidence=confidence, box=dict(x=x, y=0, width=100, height=100))


class ObjectDetectionPolicyTests(unittest.TestCase):
    def test_confidence_floor_and_stricter_settings(self):
        for value in (0.2, None, "invalid", float("nan")):
            self.assertEqual(detection_confidence(value), 0.88)
        self.assertEqual(detection_confidence(0.9), 0.9)
        self.assertEqual(detection_confidence(2), 1.0)

    def test_cross_label_duplicates_removed_but_separate_objects_remain(self):
        result = suppress_duplicates([detection(2, "truck", 89), detection(), detection(150)])
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["label"], "car")

    def test_detector_passes_nms_policy_and_filters_low_scores(self):
        from core.detector import ObjectDetector
        def box(score, x):
            coordinates = MagicMock()
            coordinates.cpu.return_value.tolist.return_value = [x, 0, x + 100, 100]
            return SimpleNamespace(conf=[score], cls=[0], xyxy=[coordinates])
        model = MagicMock()
        model.names = {0: "car"}
        model.predict.return_value = [SimpleNamespace(boxes=[box(.87, 200), box(.88, 0), box(.95, 2)])]
        detector = ObjectDetector("unused", .3, 640, model=model)
        result = detector.detect(None)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["confidence"], 95)
        self.assertEqual(model.predict.call_args.kwargs["conf"], .88)
        self.assertTrue(model.predict.call_args.kwargs["agnostic_nms"])

    def fallback(self):
        tracker = ObjectTracker.__new__(ObjectTracker)
        tracker.tracker = None
        tracker._fallback_tracks = {}
        tracker._next_id = 1
        return tracker

    def test_fallback_retains_id_through_motion_and_short_gap(self):
        tracker = self.fallback()
        first = tracker.update([detection()], None)[0]["tracking_id"]
        self.assertEqual(tracker.update([], None), [])
        current = tracker.update([detection(10), detection(12, confidence=89)], None)
        self.assertEqual(len(current), 1)
        self.assertEqual(current[0]["tracking_id"], first)

    def test_fallback_one_to_one_and_expiry(self):
        tracker = self.fallback()
        first = tracker.update([detection(), detection(150)], None)
        second = tracker.update([detection(5), detection(145)], None)
        self.assertEqual([d["tracking_id"] for d in first], [d["tracking_id"] for d in second])
        for _ in range(16):
            tracker.update([], None)
        self.assertEqual(tracker._fallback_tracks, {})
        self.assertGreater(tracker.update([detection()], None)[0]["tracking_id"], 2)

    def test_deep_sort_excludes_stale_predicted_boxes(self):
        tracker = self.fallback()
        stale = MagicMock(time_since_update=1)
        fresh = MagicMock(time_since_update=0, track_id="7")
        fresh.to_ltrb.return_value = [0, 0, 100, 100]
        fresh.get_det_class.return_value = "car"
        fresh.get_det_conf.return_value = .95
        tracker.tracker = MagicMock()
        tracker.tracker.update_tracks.return_value = [stale, fresh]
        result = tracker.update([detection()], None)
        self.assertEqual([d["tracking_id"] for d in result], [7])
        stale.to_ltrb.assert_not_called()
        fresh.to_ltrb.assert_called_once_with(orig=True, orig_strict=True)

    def test_settings_migrate_low_confidence_and_disabled_tracking(self):
        from streamlit_app import normalize_detection_settings
        settings = normalize_detection_settings(dict(confidence=.45, enable_tracking=False))
        self.assertEqual(settings["confidence"], .88)
        self.assertTrue(settings["enable_tracking"])

    def test_recording_summary_counts_repeated_id_once_even_if_label_changes(self):
        from services.detection_log_service import DetectionLogService
        with tempfile.TemporaryDirectory() as directory:
            with patch("services.detection_log_service.LOGS_DIR", Path(directory)), patch("services.detection_log_service.SNAPSHOTS_DIR", Path(directory)):
                record = {"video_id": "test"}
                logger = DetectionLogService(record)
                entries = []
                for frame, label in enumerate(("car", "car", "truck")):
                    item = {**detection(label=label), "tracking_id": 1}
                    event = dict(frame_number=frame, people_count=0, vehicle_count=1, movement_detected=False, objects=[item], plates=[])
                    entries.append((event, None))
                logger.replace_all(entries)
                self.assertEqual(record["objects_summary"]["object_counts"], {"car": 1})
                self.assertEqual(record["objects_summary"]["unique_tracking_ids"], [1])

    def test_dual_camera_tracking_state_is_separate(self):
        import numpy as np
        from core.vision_pipeline import VisionPipeline
        with patch("core.vision_pipeline.ObjectDetector") as detector, patch("core.vision_pipeline.ObjectTracker", side_effect=self.fallback):
            detector.return_value.detect.return_value = [detection()]
            pipeline = VisionPipeline("unused", .88, 640)
            frame = np.zeros((120, 120, 3), dtype=np.uint8)
            pipeline.process(frame, 1, 30, camera_role="front")
            pipeline.process(frame, 1, 30, camera_role="rear")
            front = pipeline._camera_trackers["front"]
            rear = pipeline._camera_trackers["rear"]
            self.assertIsNot(front, rear)
            self.assertEqual(front._fallback_tracks[1]["age"], 0)
            self.assertEqual(rear._fallback_tracks[1]["age"], 0)
