"""Verify schema, migrate local accounts, then enable Supabase. No plaintext passwords."""
import json
import os
from pathlib import Path
import sqlite3
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
from services.supabase_database_service import SupabaseDatabaseService
from services.user_account_service import ACCOUNT_DB


def main():
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env")
    cloud = SupabaseDatabaseService()
    cloud.check_schema()
    buckets = cloud.request("GET", "/storage/v1/bucket")
    assets = next((b for b in buckets if b["name"] == os.getenv("SUPABASE_ASSET_BUCKET", "roadwatch-assets")), None)
    if not assets or assets.get("public"):
        raise RuntimeError("The private roadwatch-assets bucket is missing. Apply the SQL migration first.")
    migrated = 0
    if ACCOUNT_DB.is_file():
        connection = sqlite3.connect(ACCOUNT_DB)
        try:
            for username, password_hash in connection.execute("SELECT username,password_hash FROM users"):
                current = cloud.find(username)
                if current and current["password_hash"] != password_hash:
                    raise RuntimeError("An account name conflicts with an existing cloud account; activation stopped without overwriting it.")
                if current is None:
                    cloud.register(username, password_hash)
                    migrated += 1
        finally:
            connection.close()
    # Activate only after all prerequisites and account migration succeed.
    env_path = root / ".env"
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    lines = [line for line in lines if not line.startswith("ROADWATCH_SUPABASE_DATABASE_ENABLED=")]
    lines.append("ROADWATCH_SUPABASE_DATABASE_ENABLED=true")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"activated": True, "accounts_migrated": migrated, "next": "Restart Roadwatch. Local records sync in the background after administrator sign-in and consent."}))


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        print(str(exc))
        raise SystemExit(1)
