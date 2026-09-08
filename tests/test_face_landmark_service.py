import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import numpy as np
from services.driver_monitoring.config import MonitoringConfig
from services.driver_monitoring.face_landmark_service import FaceLandmarkService


class LandmarkTests(unittest.TestCase):
    def test_missing_model_does_not_import_mediapipe(self):
        service=FaceLandmarkService(MonitoringConfig(landmarker_path=Path("missing.task")))
        result=service.extract(None,None,0)
        self.assertFalse(result["face_visible"])
        self.assertIsNotNone(service.error)

    def test_measurement_api_and_increasing_video_timestamps(self):
        service=FaceLandmarkService(MonitoringConfig())
        service._attempted=True
        service._mp=Mock()
        points=[SimpleNamespace(x=(i%20)/20,y=(i//20)/24) for i in range(478)]
        service._model=Mock()
        service._model.detect_for_video.return_value=SimpleNamespace(face_landmarks=[points],facial_transformation_matrixes=[np.eye(4)])
        face={"bbox":[10,10,80,80]}
        for _ in range(2):
            result=service.extract(np.zeros((100,100,3),np.uint8),face,1)
        self.assertTrue(result["face_visible"])
        self.assertIn("left_opening_ratio",result["eyes"])
        self.assertIn("opening_ratio",result["mouth"])
        self.assertEqual(result["head_pose"]["yaw"],0)
        stamps=[call.args[1] for call in service._model.detect_for_video.call_args_list]
        self.assertEqual(stamps,[1000,1001])
