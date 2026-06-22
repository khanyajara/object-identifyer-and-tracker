from pathlib import Path
from urllib.parse import quote

import requests


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

        processed_path = record.get("processed_video_path")
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
            "Content-Type": "video/mp4",
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
