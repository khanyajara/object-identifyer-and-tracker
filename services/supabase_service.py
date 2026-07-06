from pathlib import Path
from urllib.parse import quote

import requests

from core.video_io import video_mime_type


class SupabaseService:
    def __init__(self, url="", anon_key="", bucket="videos"):
        self.url = url
        self.anon_key = anon_key
        self.bucket = bucket

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
            or record.get("processed_video_path")
        )
        if not processed_path:
            raise RuntimeError("Only processed videos can be uploaded. This recording has no processed video.")

        path = Path(processed_path)
        if not path.exists() or not path.is_file():
            raise RuntimeError(f"Processed video file was not found: {path}")

        object_name = f'processed/{record["video_id"]}/{path.name}'
        bucket_path = quote(self.bucket, safe="")
        object_path = quote(object_name, safe="/")
        upload_url = f'{self.url.rstrip("/")}/storage/v1/object/{bucket_path}/{object_path}'
        headers = {
            "Authorization": f"Bearer {self.anon_key}",
            "apikey": self.anon_key,
            "Content-Type": video_mime_type(path),
            "x-upsert": "true",
        }
        with path.open("rb") as file_obj:
            response = requests.post(upload_url, headers=headers, data=file_obj, timeout=120)
        response.raise_for_status()
        return {
            "bucket": self.bucket,
            "object_name": object_name,
            "public_url": self.public_url(object_name),
        }

    def public_url(self, object_name):
        bucket_path = quote(self.bucket, safe="")
        object_path = quote(object_name, safe="/")
        return f'{self.url.rstrip("/")}/storage/v1/object/public/{bucket_path}/{object_path}'

    def refresh_video_url(self, record):
        object_name = record.get("supabase_processed_path") or record.get("supabase_object_name")
        if not object_name:
            raise RuntimeError("No Supabase object path is stored for this video.")
        return {
            "object_name": object_name,
            "public_url": self.public_url(object_name),
        }

    def list_objects(self, prefix="processed", limit=1000, offset=0):
        if not self.configured:
            raise RuntimeError("Configure Supabase URL and anon key before listing videos.")
        bucket_path = quote(self.bucket, safe="")
        url = f'{self.url.rstrip("/")}/storage/v1/object/list/{bucket_path}'
        headers = {
            "Authorization": f"Bearer {self.anon_key}",
            "apikey": self.anon_key,
            "Content-Type": "application/json",
        }
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
