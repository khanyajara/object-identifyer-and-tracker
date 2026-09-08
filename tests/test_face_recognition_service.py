import unittest
from unittest.mock import Mock
from pathlib import Path
import numpy as np
from services.driver_monitoring.config import MonitoringConfig
from services.driver_monitoring.face_recognition_service import FaceRecognitionService, normalize_embedding


class FaceRecognitionTests(unittest.TestCase):
    def setUp(self):
        self.vector = np.eye(128,dtype=np.float32)[0]
        self.service = FaceRecognitionService(MonitoringConfig(threshold=.7))
        self.service.embedding = Mock(return_value=self.vector)
        self.profile = dict(driver_id="A", display_name="Driver A", recognition_enabled=True, embedding_version="sface-v1", embedding=self.vector)

    def test_enrolled_face(self):
        result = self.service.recognize(None,None,[self.profile])
        self.assertEqual(result["driver_id"], "A")

    def test_unknown_face_never_returns_nearest_name(self):
        self.profile["embedding"] = np.eye(128)[1]
        self.assertIsNone(self.service.recognize(None,None,[self.profile]))

    def test_invalid_and_corrupted_embeddings(self):
        for value in (None, [], [0]*128, [float("nan")]*128, [float("inf")]*128, "secret-invalid", [1]*127):
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaisesRegex(ValueError, "^Invalid SFace embedding.$"):
                    normalize_embedding(value)
                self.profile["embedding"] = value
                self.assertIsNone(self.service.recognize(None,None,[self.profile]))

    def test_disabled_deleted_or_wrong_version_driver(self):
        self.assertIsNone(self.service.recognize(None,None,[]))
        for field, value in (("recognition_enabled",False),("embedding_version","old")):
            self.assertIsNone(self.service.recognize(None,None,[{**self.profile,field:value}]))

    def test_no_calibrated_threshold_skips_embedding(self):
        service = FaceRecognitionService(MonitoringConfig())
        service.embedding = Mock()
        self.assertIsNone(service.recognize(None,None,[self.profile]))
        service.embedding.assert_not_called()

    def test_two_indistinguishable_profiles_fail_closed(self):
        self.assertIsNone(self.service.recognize(None,None,[self.profile,{**self.profile,"driver_id":"B"}]))

    def test_missing_sface_model(self):
        service = FaceRecognitionService(MonitoringConfig(sface_path=Path("missing.onnx")))
        with self.assertRaisesRegex(RuntimeError, "model unavailable"):
            service.embedding(None,None)

    def test_alignment_and_normalization(self):
        model = Mock()
        model.feature.return_value = self.vector * 3
        service = FaceRecognitionService(MonitoringConfig(), model)
        face = dict(bbox=[1,2,3,4],landmarks=[[1,2]]*5,confidence=.9)
        result = service.embedding(np.zeros((8,8,3),np.uint8),face)
        self.assertAlmostEqual(np.linalg.norm(result),1)
        self.assertEqual(model.alignCrop.call_args.args[1].shape,(15,))
