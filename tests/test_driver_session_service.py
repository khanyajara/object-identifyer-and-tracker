import unittest
from services.driver_monitoring.config import MonitoringConfig
from services.driver_monitoring.driver_session_service import DriverSessionService

MATCH = dict(driver_id="A", display_name="Driver A", confidence=.8)


class DriverSessionTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.service = DriverSessionService(MonitoringConfig(), lambda:self.now)

    def sample(self, match=MATCH):
        self.now += 2
        self.service.observe(True)
        self.service.match(match)

    def test_multiple_consistent_matches_only_then_publish_identity(self):
        for _ in range(2):
            self.sample()
            self.assertIsNone(self.service.snapshot()["driver_id"])
            self.assertEqual(self.service.snapshot()["driver_display_name"],"Unknown Driver")
        self.sample()
        self.assertEqual(self.service.snapshot()["identity_status"],"verified")

    def test_consistent_mismatches_revoke_but_one_mismatch_does_not(self):
        self.sample(); self.sample(); self.sample()
        self.sample(None)
        self.assertEqual(self.service.snapshot()["driver_id"], "A")
        self.sample(None)
        self.sample(None)
        self.assertIsNone(self.service.snapshot()["driver_id"])
        self.sample(); self.sample()
        self.assertEqual(self.service.snapshot()["identity_status"],"candidate")
        self.sample()
        self.assertEqual(self.service.snapshot()["identity_status"],"verified")

    def test_lost_expires_even_without_new_frames(self):
        self.sample(); self.sample(); self.sample()
        old = self.service.snapshot()["session_id"]
        self.now += 5
        self.assertEqual(self.service.snapshot()["identity_status"],"lost")
        self.assertIsNone(self.service.metadata()["driver_id"])
        self.sample(); self.sample(); self.sample()
        self.assertNotEqual(old,self.service.snapshot()["session_id"])

    def test_brief_missing_face_keeps_cached_identity_until_timeout(self):
        self.sample(); self.sample(); self.sample()
        self.service.observe(False)
        self.assertEqual(self.service.metadata()["driver_id"], "A")
        self.now += 5
        self.assertIsNone(self.service.metadata()["driver_id"])

    def test_duplicate_timestamp_cannot_confirm(self):
        self.service.observe(True)
        for _ in range(5):
            self.service.match(MATCH)
        self.assertIsNone(self.service.snapshot()["driver_id"])

    def test_alternating_rejections_cannot_retain_wrong_verified_driver(self):
        self.sample(); self.sample(); self.sample()
        self.sample(None)
        self.sample({**MATCH, "driver_id": "B"})
        self.sample(None)
        self.assertIsNone(self.service.snapshot()["driver_id"])

    def test_context_survives_verification(self):
        self.service.context(video_id="video",vehicle_id="vehicle",trip_id="trip")
        self.sample(); self.sample(); self.sample()
        self.assertEqual(self.service.snapshot()["trip_id"],"trip")
