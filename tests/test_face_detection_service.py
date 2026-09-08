import unittest
from pathlib import Path
from unittest.mock import Mock
import numpy as np
from services.driver_monitoring.config import MonitoringConfig
from services.driver_monitoring.face_detection_service import FaceDetectionService


def row(x=240, y=150, w=160, h=180, confidence=.95):
    return np.array([x,y,w,h,x+40,y+60,x+120,y+60,x+80,y+100,x+50,y+140,x+110,y+140,confidence], np.float32)


class FaceDetectionTests(unittest.TestCase):
    def detect(self, rows):
        model = Mock()
        model.detect.return_value = (None, rows)
        service = FaceDetectionService(MonitoringConfig(), model)
        return service.detect(np.zeros((960,1280,3), np.uint8))

    def test_no_face(self):
        self.assertFalse(self.detect(None)["detected"])

    def test_one_face_rescales_box_and_five_points(self):
        result = self.detect([row()])
        self.assertTrue(result["detected"])
        self.assertEqual(result["bbox"], [480,300,320,360])
        self.assertEqual(result["landmarks"][0], [560,420])

    def test_multiple_faces_reject_ambiguous_selection(self):
        result = self.detect([row(x=180), row(x=300)])
        self.assertTrue(result["ambiguous"])
        self.assertFalse(result["detected"])

    def test_multiple_faces_select_driver_seat_over_passenger(self):
        result = self.detect([row(), row(x=10,w=60,h=70)])
        self.assertTrue(result["detected"])
        self.assertEqual(len(result["faces"]), 2)
        self.assertEqual(result["bbox"][2], 320)

    def test_missing_model_is_graceful_and_not_retried_each_frame(self):
        service = FaceDetectionService(MonitoringConfig(yunet_path=Path("does-not-exist.onnx")))
        for _ in range(2):
            self.assertFalse(service.detect(np.zeros((10,10,3), np.uint8))["detected"])
        self.assertIsNotNone(service.error)

    def test_invalid_rows_and_outside_driver_seat_are_rejected(self):
        self.assertFalse(self.detect([row(x=600), np.full(15,np.nan)])["detected"])
