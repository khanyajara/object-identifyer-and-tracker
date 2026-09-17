import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.auth_service import AdminAuthService, hash_password
from services.user_account_service import UserAccountService


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "users.sqlite3"
        self.db_patch = patch("services.user_account_service.ACCOUNT_DB", self.path)
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.env = patch.dict("os.environ", {"ROADWATCH_ADMIN_ACCOUNTS": "[]", "ROADWATCH_JWT_SECRET": "s" * 40, "ROADWATCH_SUPABASE_ACCOUNTS_ENABLED": "false"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.store = UserAccountService()
        self.password = "a-long-test-password"

    def test_registration_persists_hash_and_authenticates_as_user(self):
        self.store.register(" New.User ", self.password)
        self.assertNotIn(self.password.encode(), self.path.read_bytes())
        auth = AdminAuthService()
        account = auth.authenticate("NEW.USER", self.password)
        self.assertEqual(account, {"username": "new.user", "role": "user"})
        self.assertEqual(auth.decode_access_token(auth.issue_access_token(account)), account)
        self.assertIsNone(auth.authenticate("new.user", "incorrect"))

    def test_duplicate_and_reserved_names_rejected(self):
        self.store.register("newuser", self.password)
        with self.assertRaises(ValueError):
            self.store.register("NEWUSER", self.password)
        with patch.dict("os.environ", {"ROADWATCH_ADMIN_ACCOUNTS": json.dumps([{"username": "Admin", "password_hash": hash_password(self.password), "role": "admin"}])}):
            with self.assertRaises(ValueError):
                self.store.register("admin", self.password)

    def test_invalid_passwords_and_username_rejected(self):
        for username, password in (("../bad", self.password), ("newuser", "short"), ("newuser", "a" * 73)):
            with self.assertRaises(ValueError):
                self.store.register(username, password)
        self.assertFalse(self.path.exists())

    def test_simple_eight_character_password_is_accepted(self):
        self.store.register("simpleuser", "abcdefgh")
        self.assertIsNotNone(AdminAuthService().authenticate("simpleuser", "abcdefgh"))

    def test_registered_user_can_read_videos_but_cannot_ingest_sync(self):
        from fastapi.testclient import TestClient
        from api_app import app
        account = self.store.register("newuser", self.password)
        token = AdminAuthService().issue_access_token(account)
        with TestClient(app) as client:
            for method, path in (("get", "/videos"), ("get", "/health")):
                response = getattr(client, method)(path, headers={"Authorization": "Bearer " + token})
                self.assertEqual(response.status_code, 200)
            response = client.get("/videos/missing-test-video", headers={"Authorization": "Bearer " + token})
            self.assertIn(response.status_code, {404, 422})
            response = client.post("/videos/sync", json={"video_id": "test"}, headers={"Authorization": "Bearer " + token})
            self.assertEqual(response.status_code, 403)

    def test_signup_and_signin_forms(self):
        from streamlit.testing.v1 import AppTest
        source = Path(self.directory.name) / "app.py"
        source.write_text('''
import streamlit_app as app
app.main()
''', encoding="utf-8")
        app = AppTest.from_file(str(source), default_timeout=40).run()
        self.assertEqual(len(app.exception), 0)
        def field(label):
            return next(item for item in app.text_input if item.label == label)
        def button(label):
            return next(item for item in app.button if item.label == label)
        field("Choose a username").set_value("newuser")
        field("Create a password").set_value(self.password)
        field("Confirm password").set_value("different-password")
        button("Create account").click().run()
        self.assertTrue(any("do not match" in item.value for item in app.error))
        self.assertIsNone(self.store.find("newuser"))
        field("Choose a username").set_value("newuser")
        field("Create a password").set_value(self.password)
        field("Confirm password").set_value(self.password)
        button("Create account").click().run()
        self.assertTrue(any("Account created" in item.value for item in app.success))
        field("Username").set_value("newuser")
        field("Password").set_value(self.password)
        with patch("streamlit_app.ensure_background") as camera, patch("streamlit_app.VideoService") as videos, \
             patch("streamlit_app.privacy_permission_gate", return_value=True), \
             patch("services.supabase_sync_service.ensure_cloud_sync"), \
             patch("streamlit_app.ensure_location_tracking"), \
             patch("streamlit_app.sidebar_status"), patch("streamlit_app.system_top_bar"), \
             patch("streamlit_app.upload_status_widget"), patch("streamlit_app.dash_cam_page") as recorder, \
             patch("streamlit_app.videos_page") as video_page, \
             patch("streamlit_app.missing_person_page") as missing, \
             patch("streamlit_app.stolen_vehicle_page") as stolen:
            button("Sign in").click().run()
            self.assertEqual(len(app.exception), 0)
            self.assertFalse(app.session_state.admin_authenticated)
            options = app.sidebar.radio[0].options
            for page in ("Roadwatch", "Videos", "Report Missing Person", "Report Stolen Vehicle", "Emergency Contacts", "Vehicle Profile", "GPS Tracking"):
                self.assertIn(page, options)
            for page in ("Admin Management", "Admin Dashboard", "Settings", "Missing Persons", "Stolen Vehicles"):
                self.assertNotIn(page, options)
            camera.assert_called()
            videos.assert_called()
            recorder.assert_called()
            # Start a fresh test tree after sign-in's rerun removes the form widgets.
            token = app.session_state.admin_token
            app = AppTest.from_file(str(source), default_timeout=40)
            app.session_state.admin_token = token
            app.run()
            app.sidebar.radio[0].set_value("Videos").run()
            video_page.assert_called()
            app.sidebar.radio[0].set_value("Report Missing Person").run()
            missing.assert_called_with(admin_mode=False)
            app.sidebar.radio[0].set_value("Report Stolen Vehicle").run()
            self.assertFalse(stolen.call_args.kwargs["admin_mode"])
            app.query_params["admin"] = "true"
            app.run()
            self.assertNotIn("Admin Management", app.sidebar.radio[0].options)
            button("Sign out").click().run()
        self.assertTrue(any(item.label == "Create account" for item in app.button))

    def test_admin_login_rejects_standard_account(self):
        from streamlit.testing.v1 import AppTest
        self.store.register("newuser", self.password)
        app = AppTest.from_string("from streamlit_app import admin_login_page\nadmin_login_page()", default_timeout=40).run()
        app.text_input[0].set_value("newuser")
        app.text_input[1].set_value(self.password)
        app.button[0].click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertNotIn("admin_token", app.session_state)
        self.assertTrue(any("Invalid admin password" in item.value for item in app.error))
