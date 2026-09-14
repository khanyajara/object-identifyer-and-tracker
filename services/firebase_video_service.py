import os
from datetime import datetime
from pathlib import Path

from services.firebase_service import FirebaseService

from core.time_utils import utc_now


def normalize_video(document, document_id=None):
    item = dict(document or {})
    item["video_id"] = item.get("video_id") or document_id
    item["supabase_bucket"] = item.get("supabase_bucket") or "videos"
    item["supabase_path"] = item.get("supabase_path") or item.get("supabase_processed_path") or ""
    suffix = Path(item["supabase_path"]).suffix.lower().lstrip(".")
    item["cloud_format"] = (item.get("cloud_format") or item.get("playback_format") or suffix).lower()
    item["mime_type"] = item.get("mime_type") or {"mp4": "video/mp4", "webm": "video/webm"}.get(item["cloud_format"], "")
    item["file_size_bytes"] = int(item.get("file_size_bytes") or float(item.get("file_size_mb") or 0) * 1024 * 1024)
    item["processed"] = bool(item.get("processed"))
    item["compressed"] = bool(item.get("compressed"))
    item["firebase_sync_status"] = item.get("firebase_sync_status") or "synced"
    item["cloud_ready"] = bool(
        item.get("cloud_ready")
        or (item.get("upload_status") == "uploaded" and item["supabase_path"])
    )
    for field in ("created_at", "updated_at", "playback_url_created_at", "playback_url_expires_at"):
        if isinstance(item.get(field), datetime):
            item[field] = item[field].isoformat()
    return item


class FirebaseVideoService:
    def __init__(self, firebase):
        self.firebase = firebase

    def list_admin_cloud_videos(self, limit=20, start_after=None, status=None):
        db = self.firebase._client()
        if not db:
            raise RuntimeError("Firebase Admin credentials are required for the admin cloud catalogue.")
        from firebase_admin import firestore
        from google.cloud.firestore_v1.base_query import FieldFilter
        query = db.collection(self.firebase.video_collection)
        if status:
            query = query.where(filter=FieldFilter("upload_status", "==", status))
        else:
            query = query.where(filter=FieldFilter("upload_status", "==", "uploaded"))
            query = query.where(filter=FieldFilter("processing_status", "==", "completed"))
        query = query.order_by("created_at", direction=firestore.Query.DESCENDING)
        if start_after is not None:
            query = query.start_after(start_after)
        snapshots = list(query.limit(max(1, min(int(limit), 100))).stream(timeout=20))
        return {
            "videos": [normalize_video(snapshot.to_dict(), snapshot.id) for snapshot in snapshots],
            "next_cursor": snapshots[-1] if len(snapshots) == int(limit) else None,
            "has_more": len(snapshots) == int(limit),
        }

    def get_cloud_video(self, video_id):
        snapshot = self.firebase._client().collection(self.firebase.video_collection).document(video_id).get()
        return normalize_video(snapshot.to_dict(), snapshot.id) if snapshot.exists else None

    def save_uploaded_video(self, metadata):
        document = normalize_video(metadata)
        if not document.get("video_id"):
            raise ValueError("video_id is required.")
        required = ("supabase_bucket", "supabase_path", "mime_type")
        missing = [field for field in required if not document.get(field)]
        if missing:
            raise ValueError("Missing cloud metadata: " + ", ".join(missing))
        document.update(
            upload_status="uploaded",
            supabase_upload_status="uploaded",
            firebase_sync_status="synced",
            cloud_ready=True,
            updated_at=utc_now(),
        )
        document.setdefault("processing_status", "completed")
        document.setdefault("created_at", utc_now())
        self.firebase._client().collection(self.firebase.video_collection).document(document["video_id"]).set(document, merge=True)

    def update_playback_access(self, video_id):
        from firebase_admin import firestore
        self.firebase._client().collection(self.firebase.video_collection).document(video_id).set(
            {"last_accessed_at": utc_now(), "view_count": firestore.Increment(1)}, merge=True
        )

def _default_service():
    firebase = FirebaseService(
        project_id=os.getenv("FIREBASE_PROJECT_ID", ""),
        client_email=os.getenv("FIREBASE_CLIENT_EMAIL", ""),
        private_key=os.getenv("FIREBASE_PRIVATE_KEY", ""),
        video_collection=os.getenv("FIREBASE_VIDEO_COLLECTION", "videos"),
    )
    return FirebaseVideoService(firebase)


def list_admin_cloud_videos(limit=20, start_after=None, status=None):
    return _default_service().list_admin_cloud_videos(limit, start_after, status)


def get_cloud_video(video_id):
    return _default_service().get_cloud_video(video_id)


def save_uploaded_video(metadata):
    return _default_service().save_uploaded_video(metadata)


def update_playback_access(video_id):
    return _default_service().update_playback_access(video_id)
