"""Local self-registration. Public accounts never inherit device permissions."""
import re
import sqlite3
from pathlib import Path

ACCOUNT_DB = Path(__file__).resolve().parents[1] / "data" / "accounts" / "users.sqlite3"


class UserAccountService:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else ACCOUNT_DB
        from services.supabase_database_service import SupabaseDatabaseService
        self.cloud = SupabaseDatabaseService()
        self.use_cloud = path is None and self.cloud.enabled

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.execute("CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, password_hash TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
        return connection

    def register(self, username, password):
        from services.auth_service import _allow_login, _load_admin_accounts, hash_password
        if not _allow_login():
            raise ValueError("Too many attempts. Please try again in a minute.")
        username = (username or "").strip().casefold()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,31}", username):
            raise ValueError("Use 3–32 characters: letters, numbers, dots, underscores or hyphens. Start with a letter or number.")
        password_hash = hash_password(password)
        if any(a["username"].casefold() == username for a in _load_admin_accounts()):
            raise ValueError("That username is unavailable.")
        if self.use_cloud:
            self.cloud.register(username, password_hash)
            return {"username": username, "role": "user"}
        connection = self._connect()
        try:
            with connection:
                connection.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)", (username, password_hash))
        except sqlite3.IntegrityError:
            raise ValueError("That username is unavailable.") from None
        finally:
            connection.close()
        return {"username": username, "role": "user"}

    def find(self, username):
        if self.use_cloud:
            return self.cloud.find((username or "").strip().casefold())
        if not self.path.is_file():
            return None
        connection = self._connect()
        try:
            row = connection.execute("SELECT username, password_hash FROM users WHERE username = ?", ((username or "").strip().casefold(),)).fetchone()
            return {"username": row[0], "password_hash": row[1], "role": "user"} if row else None
        finally:
            connection.close()
