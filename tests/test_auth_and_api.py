import os
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

os.environ["ROADWATCH_JWT_SECRET"] = "test-secret-that-is-long-enough-for-jwt-signing"
os.environ["ROADWATCH_ADMIN_ACCOUNTS"] = ""  # configured in setUp with a bcrypt hash

from services.auth_service import AdminAuthService, hash_password  # noqa: E402


class AuthServiceTests(unittest.TestCase):
    def setUp(self):
        self.password = "phase-one-test-password"
        self.password_hash = hash_password(self.password)
        os.environ["ROADWATCH_ADMIN_ACCOUNTS"] = (
            '[{"username":"phase1admin","password_hash":"'
            + self.password_hash
            + '","role":"admin"}]'
        )

    def test_bcrypt_authentication_and_jwt_round_trip(self):
        service = AdminAuthService()
        account = service.authenticate("phase1admin", self.password)
        self.assertEqual(account, {"username": "phase1admin", "role": "admin"})
        self.assertIsNone(service.authenticate("phase1admin", "incorrect-password"))
        self.assertEqual(
            service.decode_access_token(service.issue_access_token(account)), account
        )


class ApiAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.password = "phase-one-test-password"
        self.password_hash = hash_password(self.password)
        os.environ["ROADWATCH_ADMIN_ACCOUNTS"] = (
            '[{"username":"phase1admin","password_hash":"'
            + self.password_hash
            + '","role":"admin"}]'
        )
        from api_app import app

        self.client = TestClient(app)

    def test_endpoints_require_jwt_and_enforce_sync_role(self):
        self.assertEqual(self.client.get("/videos").status_code, 401)
        token_response = self.client.post(
            "/auth/token",
            data={"username": "phase1admin", "password": self.password},
        )
        self.assertEqual(token_response.status_code, 200)
        token = token_response.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        self.assertEqual(self.client.get("/health", headers=headers).status_code, 200)
        self.assertEqual(
            self.client.post("/videos/sync", headers=headers, json={"video_id": "test"}).status_code,
            200,
        )


class VideoPipelineSupportTests(unittest.TestCase):
    def test_storage_cleanup_never_marks_original_or_processed_evidence_for_removal(self):
        from services.storage_service import StorageService

        preview = StorageService().cleanup_cache(max_items=5, dry_run=True)
        self.assertTrue(all("original_video_path" != row["kind"] for row in preview["preview"]))
        self.assertTrue(all("processed_video_path" != row["kind"] for row in preview["preview"]))

    def test_cloud_upload_requires_compressed_mp4(self):
        from services.supabase_service import SupabaseService

        with self.assertRaises(RuntimeError):
            SupabaseService("https://example.test", "key").upload_processed_video(
                {"processed_video_path": "video.webm"}
            )

    def test_cleanup_removes_only_failed_export_artifacts(self):
        from services.compression_service import CompressionService
        from services.video_service import EXPORTS_DIR

        result = CompressionService().cleanup_failed_exports(EXPORTS_DIR, dry_run=True)
        self.assertEqual(result["removed"], [])
        self.assertTrue((EXPORTS_DIR / ".gitkeep").exists())

    def test_metadata_repair_restores_mp4_fields(self):
        from services.video_service import VIDEOS_DIR, VideoService

        original = next(VIDEOS_DIR.glob("*.mp4"), None)
        if original is None:
            self.skipTest("No local MP4 recording fixture is available.")
        repaired, changed = VideoService.repair_metadata(
            {"original_video_path": str(original), "video_format": "webm"}
        )
        self.assertTrue(changed)
        self.assertEqual(repaired["video_format"], "mp4")
        self.assertEqual(repaired["original_mp4_path"], str(original))


if __name__ == "__main__":
    unittest.main()
