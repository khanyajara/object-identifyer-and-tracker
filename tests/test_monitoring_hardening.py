import unittest
from unittest.mock import patch
import threading
import jwt

from services.auth_service import AdminAuthService, JWT_ISSUER
from scripts.collect_driver_calibration import sample_value


class HardeningTests(unittest.TestCase):
    def test_playback_rejects_foreign_origin_and_credentials(self):
        from services.cloud_playback_service import CloudPlaybackService
        from services.supabase_service import SupabaseService
        playback = CloudPlaybackService(SupabaseService("https://project.supabase.co"))
        self.assertTrue(playback.trusted_url("https://project.supabase.co/video"))
        for url in ("https://127.0.0.1/private", "https://project.supabase.co.evil.test/video", "https://user@project.supabase.co/video", "https://project.supabase.co:444/video"):
            self.assertFalse(playback.trusted_url(url))
    def test_token_without_expiry_is_rejected(self):
        with patch.dict("os.environ", {"ROADWATCH_JWT_SECRET": "x" * 40, "ROADWATCH_ADMIN_ACCOUNTS": ""}):
            token = jwt.encode({"sub": "a", "role": "admin", "iss": JWT_ISSUER, "iat": 1}, "x" * 40, algorithm="HS256")
            with self.assertRaises(ValueError):
                AdminAuthService().decode_access_token(token)

    def test_revoked_account_token_is_rejected(self):
        with patch.dict("os.environ", {"ROADWATCH_JWT_SECRET": "x" * 40, "ROADWATCH_ADMIN_ACCOUNTS": ""}):
            auth = AdminAuthService()
            token = auth.issue_access_token({"username": "removed", "role": "admin"})
            with self.assertRaises(ValueError):
                auth.decode_access_token(token)

    def test_calibration_requires_visible_frontal_face(self):
        data = dict(face_visible=True, quality={"available": True}, head_pose=dict(yaw=0, roll=0, pitch=2), eyes=dict(left_opening_ratio=.1, right_opening_ratio=.3))
        self.assertEqual(sample_value("eyes_open", data), .3)
        data["head_pose"]["yaw"] = 40
        self.assertIsNone(sample_value("eyes_open", data))
        self.assertIsNone(sample_value("eyes_open", {}))

    def test_preview_inference_does_not_block_or_queue_batches(self):
        from core.dual_camera import DualCameraManager
        manager = DualCameraManager([])
        entered, release = threading.Event(), threading.Event()
        def slow(limit):
            entered.set()
            release.wait(5)
        with patch.object(manager, "process_live_ai", side_effect=slow) as inference:
            try:
                manager.process_preview_ai_async()
                self.assertTrue(entered.wait(2))
                for _ in range(10):
                    manager.process_preview_ai_async()
                self.assertEqual(inference.call_count, 1)
            finally:
                release.set()
                manager._preview_worker.join(2)

    def test_login_budget_is_bounded_and_recovers(self):
        from services import auth_service
        from collections import deque
        with patch.object(auth_service, "_LOGIN_ATTEMPTS", deque(maxlen=60)), patch.object(auth_service.time, "monotonic", return_value=0) as clock:
            self.assertTrue(all(auth_service._allow_login() for _ in range(60)))
            self.assertFalse(auth_service._allow_login())
            clock.return_value = 61
            self.assertTrue(auth_service._allow_login())
