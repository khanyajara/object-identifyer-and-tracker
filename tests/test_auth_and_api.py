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

    def test_video_id_cannot_escape_the_video_log_directory(self):
        service = AdminAuthService()
        token = service.issue_access_token(service.authenticate("phase1admin", self.password))
        response = self.client.get(
            "/videos/..%2F..%2Fsettings",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertIn(response.status_code, {404, 422})


class VideoPipelineSupportTests(unittest.TestCase):
    def test_browser_gps_rejects_invalid_coordinates(self):
        from services.gps_service import GPSService

        with self.assertRaises(ValueError):
            GPSService().add_browser_point(91, 0)

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

    def test_json_store_returns_an_independent_default_value(self):
        from services.local_json_service import LocalJsonStore

        from services.video_service import EXPORTS_DIR

        path = EXPORTS_DIR / ".default_isolation.json"
        path.unlink(missing_ok=True)
        try:
            store = LocalJsonStore(path, {"items": []})
            first = store.read()
            first["items"].append("mutated")
            self.assertEqual(store.read(), {"items": []})
        finally:
            path.unlink(missing_ok=True)

    def test_settings_writes_do_not_persist_credentials(self):
        import streamlit_app
        from services.video_service import EXPORTS_DIR

        original_path = streamlit_app.SETTINGS_PATH
        test_path = EXPORTS_DIR / ".settings_security_test.json"
        streamlit_app.SETTINGS_PATH = test_path
        try:
            streamlit_app.save_settings(
                {
                    "camera_index": 0,
                    "supabase_anon_key": "must-not-be-written",
                    "notification_smtp_password": "must-not-be-written",
                }
            )
            content = test_path.read_text(encoding="utf-8")
            self.assertNotIn("must-not-be-written", content)
            self.assertIn('"camera_index": 0', content)
        finally:
            streamlit_app.SETTINGS_PATH = original_path
            test_path.unlink(missing_ok=True)

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
