"""Server-only Supabase records. Never use anonymous credentials for private data."""
import os
from urllib.parse import urlparse
import requests


class SupabaseDatabaseService:
    def __init__(self):
        self.url = os.getenv("SUPABASE_URL", "").rstrip("/")
        self.key = os.getenv("SUPABASE_SECRET_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

    @property
    def enabled(self):
        return os.getenv("ROADWATCH_SUPABASE_DATABASE_ENABLED", "false").lower() == "true"

    def request(self, method, path, *, payload=None, params=None, prefer=None):
        if not self.key or urlparse(self.url).scheme != "https":
            raise RuntimeError("Supabase database requires an HTTPS URL and a server secret key.")
        headers = {"apikey": self.key, "Content-Type": "application/json"}
        if not self.key.startswith("sb_"):
            headers["Authorization"] = "Bearer " + self.key
        if prefer:
            headers["Prefer"] = prefer
        try:
            response = requests.request(method, self.url + path, headers=headers, json=payload, params=params, timeout=15, allow_redirects=False)
        except requests.RequestException:
            raise RuntimeError("Supabase is unreachable; local records remain available.") from None
        if response.status_code == 409:
            raise ValueError("That username is unavailable.")
        if not 200 <= response.status_code < 300:
            raise RuntimeError(f"Supabase rejected the operation (HTTP {response.status_code}). Check the migration and server permissions.")
        return response.json() if response.content else None

    def register(self, username, password_hash, ignore_existing=False):
        self.request("POST", "/rest/v1/roadwatch_accounts", payload={"username": username, "password_hash": password_hash},
                     prefer="resolution=ignore-duplicates,return=minimal" if ignore_existing else "return=minimal")

    def find(self, username):
        rows = self.request("GET", "/rest/v1/roadwatch_accounts", params={"username": "eq." + username, "select": "username,password_hash", "limit": 1})
        return {**rows[0], "role": "user"} if rows else None

    def upsert_record(self, device_id, record_id, kind, payload):
        self.request("POST", "/rest/v1/roadwatch_records", payload={"device_id": device_id, "record_id": record_id, "kind": kind, "payload": payload}, prefer="resolution=merge-duplicates,return=minimal")

    def check_schema(self):
        for table, column in (("roadwatch_accounts", "username"), ("roadwatch_records", "record_id")):
            self.request("GET", "/rest/v1/" + table, params={"select": column, "limit": 0})

    def list_videos(self, limit=80):
        rows = self.request("GET", "/rest/v1/roadwatch_records", params={"select": "payload", "kind": "eq.logs", "payload->>supabase_processed_path": "not.is.null", "limit": max(1, min(limit, 100)), "order": "updated_at.desc"})
        videos = []
        for row in rows:
            item = row["payload"]
            if not isinstance(item, dict) or not item.get("video_id") or not item.get("supabase_processed_path"):
                continue
            videos.append({**item, "title": item.get("filename") or item["video_id"], "created_at": item.get("started_at") or "", "processed": True, "upload_status": item.get("supabase_upload_status", "uploaded"), "plate_results": item.get("objects_summary", {}).get("plates_detected", [])})
        return videos
