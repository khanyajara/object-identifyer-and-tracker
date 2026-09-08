import time
import unittest
from unittest.mock import Mock, patch
import numpy as np
from services.driver_monitoring.config import MonitoringConfig
from services.driver_monitoring.driver_monitoring_service import DriverMonitoringService
from services.driver_monitoring.runtime import DriverMonitoringRuntime

FACE = dict(detected=True, ambiguous=False, bbox=[100,100,150,180],landmarks=[],confidence=.95)
MATCH = dict(driver_id="A",display_name="Driver A",confidence=.8)


class MonitoringTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.detector = Mock(error=None)
        self.detector.detect.return_value = FACE.copy()
        self.recognizer = Mock()
        self.recognizer.recognize.return_value = MATCH
        self.landmarker = Mock(error=None)
        self.landmarker.extract.return_value = dict(face_visible=True,eyes={},mouth={},head_pose={},quality={})
        self.store = Mock(revision=0)
        self.store.recognition_profiles.return_value = [{"driver_id": "A"}]
        self.frame = np.zeros((480,640,3),np.uint8)
        self.service = self.make_service()

    def make_service(self, **options):
        service = DriverMonitoringService(MonitoringConfig(enabled=True,threshold=.7,**options),self.detector,self.recognizer,self.landmarker,self.store,lambda:self.now)
        service.set_active(True)
        return service

    def tick(self, now):
        self.now = now
        return self.service.process_frame(self.frame)

    def test_independent_schedules_and_verified_rechecks(self):
        for now in (0,.02,.05):
            self.tick(now)
        self.assertEqual(self.detector.detect.call_count,1)
        self.assertEqual(self.recognizer.recognize.call_count,1)
        self.tick(2); result=self.tick(4)
        self.assertEqual(result["identity_status"],"verified")
        for now in (5,6,7,8):
            self.tick(now)
        self.assertEqual(self.recognizer.recognize.call_count,3)
        self.tick(11)
        self.assertEqual(self.recognizer.recognize.call_count,4)

    def test_no_face_skips_recognition_and_landmarks(self):
        self.detector.detect.return_value = {"detected":False}
        result=self.tick(0)
        self.assertEqual(result["identity_status"],"no_face")
        self.recognizer.recognize.assert_not_called()
        self.landmarker.extract.assert_not_called()

    def test_recognition_disabled_preserves_landmarks(self):
        self.service = self.make_service(recognition_enabled=False)
        result=self.tick(0)
        self.assertTrue(result["landmarks_available"])
        self.assertIsNone(result["driver_id"])
        self.recognizer.recognize.assert_not_called()

    def test_database_failure_revokes_identity_without_throwing(self):
        self.tick(0); self.tick(2); self.tick(4)
        self.store.recognition_profiles.side_effect=RuntimeError("private database error")
        self.service.index.invalidate()
        for now in range(5,12):
            result=self.tick(now)
        self.assertIsNone(result["driver_id"])
        self.assertNotIn("private database error",str(result))

    def test_landmark_failure_does_not_stop_identity(self):
        self.landmarker.extract.side_effect=RuntimeError()
        self.tick(0); self.tick(2); result=self.tick(4)
        self.assertEqual(result["driver_id"],"A")
        self.assertFalse(result["landmarks_available"])

    def test_detector_failure_and_lost_face(self):
        self.tick(0); self.tick(2); self.tick(4)
        self.detector.detect.side_effect=RuntimeError()
        self.assertIsNone(self.tick(5)["driver_id"])
        self.now=10
        self.assertEqual(self.service.process_frame(None)["identity_status"],"lost")

    def test_primary_face_switch_restarts_confirmation(self):
        self.tick(0); self.tick(2)
        self.detector.detect.return_value={**FACE,"bbox":[350,100,150,180]}
        self.assertIsNone(self.tick(4)["driver_id"])

    def test_slow_database_does_not_certify_stale_frame(self):
        def slow_read():
            self.now += 10
            return []
        self.store.recognition_profiles.side_effect=slow_read
        self.assertIsNone(self.tick(0)["driver_id"])

    def test_streamlit_rerun_reuses_runtime_and_model_adapters(self):
        from services.driver_monitoring import runtime
        with patch.object(runtime,"_runtime",None), patch.object(runtime.MonitoringConfig,"from_env",return_value=MonitoringConfig()):
            a=runtime.get_runtime(); b=runtime.get_runtime()
            self.assertIs(a,b)
            self.assertIs(a.service.detector,b.service.detector)
            self.assertFalse(a.service.detector._attempted)

    def test_runtime_nonblocking_start_stop_and_fresh_frames_only(self):
        runtime=DriverMonitoringRuntime(MonitoringConfig(enabled=True,lost_timeout=.15))
        runtime.service=self.make_service()
        runtime.service.process_frame=Mock(return_value=runtime.service.result())
        source=Mock(return_value=(self.frame,1,time.time()))
        started=time.perf_counter()
        runtime.start(source=source)
        self.assertLess(time.perf_counter()-started,.1)
        deadline=time.monotonic()+1
        while runtime.service.process_frame.call_count==0 and time.monotonic()<deadline:
            time.sleep(.01)
        time.sleep(.04)
        self.assertEqual(runtime.service.process_frame.call_count,1)
        thread=runtime._thread
        runtime.start(source=source)
        self.assertIs(runtime._thread,thread)
        runtime.stop()
        thread.join(timeout=1)
        self.assertFalse(thread.is_alive())

    def test_runtime_rejects_road_camera_collision(self):
        runtime=DriverMonitoringRuntime(MonitoringConfig(enabled=True))
        with patch.dict("os.environ",{"DRIVER_CAMERA_INDEX":"0"}):
            runtime.start(excluded_indices={0})
        self.assertFalse(runtime.running)
