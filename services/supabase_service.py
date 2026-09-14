from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote
import os

import requests

from core.video_io import video_mime_type


class SupabaseService:
    def __init__(
        self,
        url="",
        anon_key="",
        bucket="videos",
        bucket_public=None,
        signed_url_expiry_seconds=3600,
    ):
        self.url = url
        self.anon_key = anon_key
        if url and url.rstrip("/") == os.getenv("SUPABASE_URL", "").rstrip("/"):
            self.anon_key = os.getenv("SUPABASE_SECRET_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY") or anon_key
        self.bucket = bucket
        self.bucket_public = (os.getenv("SUPABASE_VIDEO_BUCKET_PUBLIC", "false").lower() == "true") if bucket_public is None else bool(bucket_public)
        try:
            expiry = int(signed_url_expiry_seconds)
        except (TypeError, ValueError):
            expiry = 3600
        self.signed_url_expiry_seconds = max(1, expiry)

    def _headers(self, **extra):
        headers = {"apikey": self.anon_key, **extra}
        if not self.anon_key.startswith("sb_"):
            headers["Authorization"] = "Bearer " + self.anon_key
        return headers

    @property
    def configured(self):
        return bool(self.url and self.anon_key)

    def status(self):
        return {
            "configured": self.configured,
            "bucket": self.bucket,
            "message": (
                "Supabase placeholders configured"
                if self.configured
                else "Local JSON storage active; Supabase not configured"
            ),
        }

    def upload_processed_video(self, record):
        if not self.configured:
            raise RuntimeError("Configure Supabase URL and anon key before uploading.")

        processed_path = (
            record.get("upload_video_path")
            or record.get("compressed_processed_path")
            or record.get("compressed_mp4_path")
            or record.get("processed_video_path")
            or record.get("processed_mp4_path")
        )
        if not processed_path:
            raise RuntimeError("Only processed videos can be uploaded. This recording has no processed video.")

        path = Path(processed_path)
        if not path.exists() or not path.is_file():
            raise RuntimeError(f"Processed video file was not found: {path}")

        if path.suffix.lower() != ".mp4":
            raise RuntimeError("Cloud uploads require the compressed MP4 playback file.")
        upload_path = path
        upload_format = "mp4"

        object_name = f'processed/{record["video_id"]}/{upload_path.name}'
        bucket_path = quote(self.bucket, safe="")
        object_path = quote(object_name, safe="/")
        upload_url = f'{self.url.rstrip("/")}/storage/v1/object/{bucket_path}/{object_path}'
        headers = self._headers(**{"Content-Type": video_mime_type(upload_path), "x-upsert": "true"})
        with upload_path.open("rb") as file_obj:
            response = requests.post(upload_url, headers=headers, data=file_obj, timeout=120)
        response.raise_for_status()
        payload = {
            "bucket": self.bucket,
            "object_name": object_name,
            "public_url": self.create_playback_url(object_name)["url"],
            "upload_path": str(upload_path),
            "upload_format": upload_format,
        }
        payload["mp4_path"] = str(upload_path)
        payload["mp4_url"] = payload["public_url"]
        return payload

    def public_url(self, object_name):
        bucket_path = quote(self.bucket, safe="")
        object_path = quote(object_name, safe="/")
        return f'{self.url.rstrip("/")}/storage/v1/object/public/{bucket_path}/{object_path}'

    def create_playback_url(self, object_name, expires_in=None):
        """Return a public URL or create a temporary URL for a private bucket."""
        if not self.configured:
            raise RuntimeError("Configure Supabase URL and anon key before creating playback URLs.")
        if not object_name or not str(object_name).strip("/"):
            raise ValueError("A Supabase object path is required.")

        now = datetime.now(timezone.utc)
        if self.bucket_public:
            return {
                "url": self.public_url(object_name),
                "created_at": now.isoformat(),
                "expires_at": None,
            }

        try:
            ttl = int(expires_in or self.signed_url_expiry_seconds)
        except (TypeError, ValueError):
            ttl = self.signed_url_expiry_seconds
        ttl = max(1, ttl)
        bucket_path = quote(self.bucket, safe="")
        object_path = quote(str(object_name).strip("/"), safe="/")
        url = f'{self.url.rstrip("/")}/storage/v1/object/sign/{bucket_path}/{object_path}'
        headers = self._headers(**{"Content-Type": "application/json"})
        response = requests.post(url, headers=headers, json={"expiresIn": ttl}, timeout=30)
        response.raise_for_status()
        payload = response.json()
        signed_path = payload.get("signedURL") or payload.get("signedUrl")
        if not signed_path:
            raise RuntimeError("Supabase did not return a signed playback URL.")
        signed_url = signed_path if str(signed_path).startswith("http") else f'{self.url.rstrip("/")}/storage/v1{signed_path}'
        return {
            "url": signed_url,
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=ttl)).isoformat(),
        }

    def refresh_video_url(self, record):
        object_name = record.get("supabase_processed_path") or record.get("supabase_object_name")
        if not object_name:
            raise RuntimeError("No Supabase object path is stored for this video.")
        playback = self.create_playback_url(object_name)
        return {
            "object_name": object_name,
            "public_url": playback["url"],
            "created_at": playback.get("created_at"),
            "expires_at": playback.get("expires_at"),
        }

    def list_objects(self, prefix="processed", limit=1000, offset=0):
        if not self.configured:
            raise RuntimeError("Configure Supabase URL and anon key before listing videos.")
        bucket_path = quote(self.bucket, safe="")
        url = f'{self.url.rstrip("/")}/storage/v1/object/list/{bucket_path}'
        headers = self._headers(**{"Content-Type": "application/json"})
        payload = {
            "prefix": prefix.strip("/"),
            "limit": int(limit),
            "offset": int(offset),
            "sortBy": {"column": "created_at", "order": "desc"},
        }
        response = requests.post(url, headers=headers, json=payload, timeout=60)
        response.raise_for_status()
        return response.json()

    def list_video_objects(self, prefix="processed"):
        videos = []
        stack = [prefix.strip("/")]
        seen_prefixes = set()
        video_extensions = {".mp4", ".webm", ".avi", ".mov", ".mkv"}
        while stack:
            current_prefix = stack.pop()
            if current_prefix in seen_prefixes:
                continue
            seen_prefixes.add(current_prefix)
            for item in self.list_objects(current_prefix):
                name = item.get("name", "")
                if not name:
                    continue
                object_path = f"{current_prefix}/{name}".strip("/")
                suffix = Path(name).suffix.lower()
                metadata = item.get("metadata") or {}
                if suffix in video_extensions:
                    videos.append(
                        {
                            "name": name,
                            "object_name": object_path,
                            "bucket": self.bucket,
                            "public_url": self.public_url(object_path),
                            "size": item.get("size") or metadata.get("size") or 0,
                            "created_at": item.get("created_at") or item.get("updated_at"),
                            "updated_at": item.get("updated_at") or item.get("created_at"),
                            "metadata": metadata,
                        }
                    )
                elif "." not in name:
                    stack.append(object_path)
        return videos
