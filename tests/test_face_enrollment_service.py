import unittest
from unittest.mock import Mock
import numpy as np
from services.driver_monitoring.config import MonitoringConfig
from services.driver_monitoring.face_enrollment_service import FaceEnrollmentService
from services.driver_monitoring.biometric_store import BiometricStore

ADMIN={"username":"admin","role":"admin"}


class EnrollmentTests(unittest.TestCase):
    def setUp(self):
        self.detector=Mock()
        self.face=dict(detected=True,bbox=[80,80,100,110],landmarks=[[100,100],[150,100],[130,130],[110,160],[150,160]])
        self.face["faces"]=[{}]
        self.detector.detect.return_value=self.face
        self.recognizer=Mock(config=MonitoringConfig(threshold=.7))
        self.recognizer.embedding.return_value=np.eye(128)[0]
        self.store=Mock()
        self.store.save_enrollment.return_value={"driver_id":"A"}
        self.service=FaceEnrollmentService(self.detector,self.recognizer,self.store)
        self.frame=np.random.default_rng(1).integers(40,220,(240,320,3),dtype=np.uint8)

    def test_admin_only(self):
        for actor in (None,{"role":"operator"},{"role":"viewer"}):
            with self.assertRaises(PermissionError):
                self.service.start(actor,"A",0)
            with self.assertRaises(PermissionError):
                BiometricStore().list_drivers(actor)

    def test_single_bad_or_duplicate_sample_cannot_enroll(self):
        self.service.start(ADMIN,"A",0)
        self.service.process(self.frame,1)
        for now in range(2,8):
            self.service.process(self.frame,now)
        self.assertEqual(self.service.status["samples"],1)
        self.store.save_enrollment.assert_not_called()

    def test_quality_checks_and_multiple_faces(self):
        self.service.start(ADMIN,"A",0)
        self.service.process(np.zeros_like(self.frame),1)
        self.assertEqual(self.service.status["samples"],0)
        self.face["faces"]=[{},{}]
        self.service.process(self.frame,2)
        self.assertEqual(self.service.status["samples"],0)

    def test_five_consistent_varied_samples_save_only_embedding(self):
        self.service.start(ADMIN,"A",0)
        for now in range(1,6):
            self.face["landmarks"][2][0]=126+now
            self.service.process(np.roll(self.frame,now,axis=0),now)
        self.assertEqual(self.service.status["status"],"complete")
        vector=self.store.save_enrollment.call_args.args[2]
        self.assertEqual(vector.shape,(128,))
        self.assertAlmostEqual(float(np.linalg.norm(vector)),1)
        self.assertEqual(self.service._samples,[])
        self.assertNotIn("embedding",self.service.status)

    def test_timeout_discards_private_samples(self):
        self.service.start(ADMIN,"A",0)
        self.service.process(self.frame,1)
        self.service.process(None,61)
        self.assertEqual(self.service.status["status"],"failed")
        self.assertEqual(self.service._samples,[])

    def test_private_store_has_no_public_or_local_fallback(self):
        from unittest.mock import patch
        firebase=Mock()
        with patch.dict("os.environ",{"DRIVER_BIOMETRICS_RULES_CONFIGURED":"false"}):
            with self.assertRaises(RuntimeError):
                BiometricStore(firebase).recognition_profiles()
        firebase._client.assert_not_called()

    def test_delete_only_biometric_and_enrollment_fields(self):
        from unittest.mock import patch
        db=Mock()
        with patch.dict("os.environ",{"DRIVER_BIOMETRICS_RULES_CONFIGURED":"true"}):
            store=BiometricStore(Mock(_client=Mock(return_value=db)))
            store.delete_enrollment(ADMIN,"A")
        self.assertEqual(db.batch().delete.call_count,1)
        fields=db.batch().update.call_args.args[1]
        self.assertEqual(set(fields),{"recognition_enabled","enrollment_status","embedding_version"})
