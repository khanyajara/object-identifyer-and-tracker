import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from services.supabase_database_service import SupabaseDatabaseService
from services.supabase_sync_service import SupabaseSyncService, clean_payload


class DatabaseSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cloud = Mock(enabled=True, url="https://example.supabase.co", key="sb_secret_test")
        self.sync = SupabaseSyncService(self.root, self.cloud)

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_changed_records_retry_and_survive_worker_restart(self):
        self.write("contacts/contacts.json", [{"name": "Example"}])
        self.cloud.upsert_record.side_effect = RuntimeError("offline")
        self.assertEqual(self.sync.run_once()["errors"], 1)
        self.cloud.upsert_record.side_effect = None
        self.assertEqual(self.sync.run_once()["uploaded"], 1)
        self.assertEqual(SupabaseSyncService(self.root, self.cloud).run_once()["uploaded"], 0)
        self.write("contacts/contacts.json", [])
        self.assertEqual(self.sync.run_once()["uploaded"], 1)

    def test_secrets_and_biometric_folders_not_synced(self):
        self.write("accounts/users.json", {"password_hash": "never-send"})
        self.write("driver_biometrics/private.json", {"embedding": [1, 2]})
        self.write("notifications/notification_settings.json", {"smtp_password": "never-send"})
        self.write("incidents/incidents.json", {"event": 1, "nested": {"password": "never-send", "api_key": "never-send"}})
        self.assertEqual(self.sync.run_once()["uploaded"], 1)
        self.assertNotIn("never-send", repr(self.cloud.upsert_record.call_args))

    def test_jsonl_chunk_manifest_handles_detection_events(self):
        path = self.root / "logs" / "events.jsonl"
        path.parent.mkdir()
        path.write_text("\n".join(json.dumps({"frame": n}) for n in range(201)))
        self.assertEqual(self.sync.run_once()["uploaded"], 1)
        self.assertEqual(self.cloud.upsert_record.call_count, 4)
        self.assertEqual(self.cloud.upsert_record.call_args.args[-1], {"chunks": 3, "format": "jsonl"})

    def test_disabled_sync_performs_no_network_or_state_write(self):
        self.cloud.enabled = False
        self.assertFalse(self.sync.run_once()["enabled"])
        self.cloud.upsert_record.assert_not_called()
        self.assertFalse(self.sync.state_dir.exists())

    def test_cloud_registration_never_falls_back_to_local_when_offline(self):
        from services.user_account_service import UserAccountService
        with patch("services.supabase_database_service.SupabaseDatabaseService", return_value=self.cloud), patch.dict("os.environ", {"ROADWATCH_ADMIN_ACCOUNTS": "[]"}):
            accounts = UserAccountService()
            self.cloud.register.side_effect = RuntimeError("offline")
            with self.assertRaises(RuntimeError), patch.object(accounts, "_connect") as local:
                accounts.register("newuser", "abcdefgh")
            local.assert_not_called()

    def test_accounts_persist_independently_of_record_sync(self):
        from services.user_account_service import UserAccountService
        from services.auth_service import verify_password
        rows = {}
        self.cloud.enabled = False
        self.cloud.register.side_effect = lambda username, password_hash, **kwargs: rows.setdefault(username, {"username": username, "password_hash": password_hash})
        self.cloud.find.side_effect = rows.get
        with patch("services.supabase_database_service.SupabaseDatabaseService", return_value=self.cloud), patch("services.user_account_service.ACCOUNT_DB", self.root / "absent.sqlite3"), patch.dict("os.environ", {"ROADWATCH_SUPABASE_ACCOUNTS_ENABLED": "auto", "ROADWATCH_ADMIN_ACCOUNTS": "[]"}):
            first = UserAccountService()
            self.assertTrue(first.use_cloud)
            first.register("durableuser", "persistent password")
            second = UserAccountService()
            self.assertTrue(verify_password("persistent password", second.find("durableuser")["password_hash"]))
            self.assertFalse(second.path.exists())

    def test_legacy_migration_preserves_existing_cloud_password(self):
        import sqlite3
        from services.user_account_service import UserAccountService
        database = self.root / "legacy.sqlite3"
        with sqlite3.connect(database) as connection:
            connection.execute("CREATE TABLE users(username TEXT, password_hash TEXT)")
            connection.execute("INSERT INTO users VALUES ('legacyuser', 'original-hash')")
        connection.close()
        with patch("services.supabase_database_service.SupabaseDatabaseService", return_value=self.cloud), patch("services.user_account_service.ACCOUNT_DB", database), patch.dict("os.environ", {"ROADWATCH_SUPABASE_ACCOUNTS_ENABLED": "true"}):
            UserAccountService().find("legacyuser")
            self.cloud.register.assert_called_once_with("legacyuser", "original-hash", ignore_existing=True)
            UserAccountService().find("legacyuser")
            self.cloud.register.assert_called_once()

    def test_new_secret_key_not_sent_as_bearer_jwt(self):
        with patch.dict("os.environ", {"SUPABASE_URL": "https://example.supabase.co", "SUPABASE_SECRET_KEY": "sb_secret_test"}), patch("services.supabase_database_service.requests.request") as request:
            request.return_value = Mock(status_code=201, content=b"")
            SupabaseDatabaseService().register("newuser", "$2b$hash")
            headers = request.call_args.kwargs["headers"]
            self.assertNotIn("Authorization", headers)
            self.assertEqual(headers["apikey"], "sb_secret_test")

    def test_private_upload_returns_signed_url(self):
        from services.supabase_service import SupabaseService
        path = self.root / "clip.mp4"
        path.write_bytes(b"test-video")
        service = SupabaseService("https://example.supabase.co", "key", bucket_public=False)
        with patch("services.supabase_service.requests.post") as upload, patch.object(service, "create_playback_url", return_value={"url": "https://example.supabase.co/signed"}):
            result = service.upload_processed_video({"video_id": "vid-test", "upload_video_path": str(path)})
            self.assertEqual(result["public_url"], "https://example.supabase.co/signed")
            upload.return_value.raise_for_status.assert_called_once()


if __name__ == "__main__":
    unittest.main()
