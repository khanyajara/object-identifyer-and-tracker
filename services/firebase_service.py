from datetime import datetime, timezone

import requests


class FirebaseService:
    def __init__(self, push_url="", api_key=""):
        self.push_url = push_url.rstrip("/")
        self.api_key = api_key

    @property
    def configured(self):
        return bool(self.push_url)

    def push_video_link(self, record, processed_video_url):
        if not self.configured:
            return {
                "configured": False,
                "message": "Firebase push URL not configured; link stored locally only.",
            }

        url = self.push_url
        if self.api_key:
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}auth={self.api_key}"

        payload = {
            "video_id": record.get("video_id"),
            "filename": record.get("filename"),
            "processed_video_url": processed_video_url,
            "supabase_processed_url": processed_video_url,
            "original_video_path": record.get("original_video_path"),
            "started_at": record.get("started_at"),
            "ended_at": record.get("ended_at"),
            "duration_seconds": record.get("duration_seconds"),
            "objects_summary": record.get("objects_summary", {}),
            "sync_status": "Synced",
            "pushed_at": datetime.now(timezone.utc).isoformat(),
        }
        response = requests.post(url, json=payload, timeout=30)
        response.raise_for_status()
        return response.json() if response.content else {"status": "pushed"}
