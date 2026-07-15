"""Environment-backed authentication and JWT helpers for Roadwatch."""

import argparse
import json
import os
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt


VALID_ROLES = {"viewer", "operator", "admin", "super_admin"}
JWT_ALGORITHM = "HS256"
JWT_ISSUER = "roadwatch-vision-recorder"


def hash_password(password):
    """Create a bcrypt hash for one-time administrator provisioning."""
    if not password or len(password) < 12:
        raise ValueError("Admin passwords must contain at least 12 characters.")
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password, password_hash):
    if not password or not password_hash:
        return False
    try:
        return bcrypt.checkpw(
            password.encode("utf-8"), password_hash.encode("utf-8")
        )
    except (TypeError, ValueError):
        return False


def _load_admin_accounts():
    """Load JSON account configuration from the environment, never from source."""
    raw_accounts = os.getenv("ROADWATCH_ADMIN_ACCOUNTS", "").strip()
    if not raw_accounts:
        return []
    try:
        accounts = json.loads(raw_accounts)
    except json.JSONDecodeError as exc:
        raise ValueError("ROADWATCH_ADMIN_ACCOUNTS must be valid JSON.") from exc
    if not isinstance(accounts, list):
        raise ValueError("ROADWATCH_ADMIN_ACCOUNTS must be a JSON array.")

    valid_accounts = []
    for account in accounts:
        if not isinstance(account, dict):
            raise ValueError("Each admin account must be a JSON object.")
        username = str(account.get("username", "")).strip()
        password_hash = str(account.get("password_hash", "")).strip()
        role = str(account.get("role", "")).strip().lower()
        if not username or not password_hash.startswith("$2") or role not in VALID_ROLES:
            raise ValueError(
                "Each admin requires username, a bcrypt password_hash, and a valid role."
            )
        valid_accounts.append(
            {"username": username, "password_hash": password_hash, "role": role}
        )
    return valid_accounts


def _jwt_secret():
    secret = os.getenv("ROADWATCH_JWT_SECRET", "")
    if len(secret) < 32:
        raise RuntimeError(
            "ROADWATCH_JWT_SECRET must be configured with at least 32 characters."
        )
    return secret


def _token_minutes():
    try:
        minutes = int(os.getenv("ROADWATCH_JWT_EXPIRES_MINUTES", "60"))
    except ValueError as exc:
        raise ValueError("ROADWATCH_JWT_EXPIRES_MINUTES must be a whole number.") from exc
    return max(1, min(minutes, 1440))


class AdminAuthService:
    def __init__(self):
        self.admins = _load_admin_accounts()

    def authenticate(self, username, password):
        username = (username or "").strip()
        for admin in self.admins:
            if admin["username"] == username and verify_password(
                password, admin["password_hash"]
            ):
                return {"username": admin["username"], "role": admin["role"]}
        return None

    def list_admins(self):
        return [
            {"username": admin["username"], "role": admin["role"]}
            for admin in self.admins
        ]

    def development_credentials_enabled(self):
        return False

    def issue_access_token(self, account):
        if not account or account.get("role") not in VALID_ROLES:
            raise ValueError("A valid account is required to issue an access token.")
        now = datetime.now(timezone.utc)
        payload = {
            "sub": account["username"],
            "role": account["role"],
            "iss": JWT_ISSUER,
            "iat": now,
            "exp": now + timedelta(minutes=_token_minutes()),
        }
        return jwt.encode(payload, _jwt_secret(), algorithm=JWT_ALGORITHM)

    def decode_access_token(self, token):
        try:
            claims = jwt.decode(
                token,
                _jwt_secret(),
                algorithms=[JWT_ALGORITHM],
                issuer=JWT_ISSUER,
            )
        except jwt.PyJWTError as exc:
            raise ValueError("Invalid or expired access token.") from exc
        if not claims.get("sub") or claims.get("role") not in VALID_ROLES:
            raise ValueError("Access token is missing a valid subject or role.")
        return {"username": claims["sub"], "role": claims["role"]}


def _main():
    parser = argparse.ArgumentParser(description="Roadwatch administrator helpers")
    parser.add_argument("--hash-password", action="store_true")
    args = parser.parse_args()
    if args.hash_password:
        import getpass

        password = getpass.getpass("Password (minimum 12 characters): ")
        confirmation = getpass.getpass("Confirm password: ")
        if password != confirmation:
            raise SystemExit("Passwords did not match.")
        print(hash_password(password))
        return
    parser.print_help()


if __name__ == "__main__":
    _main()
