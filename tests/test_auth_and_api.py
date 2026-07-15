import os
import unittest

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


if __name__ == "__main__":
    unittest.main()
