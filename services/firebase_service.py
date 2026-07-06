from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class FirebaseService:
    def __init__(
        self,
        push_url="",
        api_key="",
        project_id="",
        client_email="",
        private_key="",
        video_collection="videos",
        auth_domain="",
        storage_bucket="",
        app_id="",
    ):
        self.push_url = (push_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.project_id = project_id or ""
        self.client_email = client_email or ""
        self.private_key = (private_key or "").replace("\\n", "\n")
        self.video_collection = video_collection or "videos"
        self.auth_domain = auth_domain or ""
        self.storage_bucket = storage_bucket or ""
        self.app_id = app_id or ""
        self._firebase_error = None
        self._db = None

    @property
    def admin_configured(self):
        return bool(self.project_id and self.client_email and self.private_key)

    @property
    def configured(self):
        return self.admin_configured or self.rest_configured or bool(self.push_url)

    @property
    def rest_configured(self):
        return bool(self.project_id and self.api_key)

    def status(self):
        if self.admin_configured and self._client():
            return {
                "configured": True,
                "mode": "firestore",
                "message": "Firebase Firestore sync configured.",
            }
        if self.push_url:
            return {
                "configured": True,
                "mode": "push_url",
                "message": "Firebase push URL configured.",
            }
        if self.rest_configured:
            return {
                "configured": True,
                "mode": "firestore_rest",
                "message": "Firebase web config loaded. Firestore REST sync will work if security rules allow it.",
            }
        return {
            "configured": False,
            "mode": "local",
            "message": "Firebase sync unavailable. Local mode active.",
            "error": self._firebase_error,
        }

    def build_video_document(self, record, supabase_url="", supabase_path=""):
        summary = record.get("objects_summary", {}) or {}
        processed_path = (
            record.get("upload_video_path")
            or record.get("compressed_processed_path")
            or record.get("processed_video_path")
            or ""
        )
        file_size_mb = 0
        if processed_path and Path(processed_path).exists():
            file_size_mb = round(Path(processed_path).stat().st_size / (1024 * 1024), 2)
        return {
            "video_id": record.get("video_id"),
            "title": record.get("filename") or record.get("video_id"),
            "created_at": record.get("started_at") or utc_now(),
            "updated_at": utc_now(),
            "source": "roadwatch",
            "status": "processed_uploaded",
            "supabase_bucket": record.get("supabase_bucket", "videos"),
            "supabase_path": supabase_path or record.get("supabase_processed_path") or "",
            "supabase_url": supabase_url or record.get("supabase_processed_url") or "",
            "supabase_webm_url": record.get("supabase_webm_url", ""),
            "supabase_mp4_url": record.get("supabase_mp4_url", ""),
            "playback_source": record.get("playback_source") or record.get("supabase_webm_url") or record.get("supabase_mp4_url") or supabase_url or record.get("supabase_processed_url") or "",
            "playback_format": record.get("playback_format", ""),
            "processed": bool(record.get("processed_video_path")),
            "compressed": bool(record.get("compressed_processed_path")),
            "duration_seconds": record.get("duration_seconds", 0),
            "file_size_mb": file_size_mb,
            "thumbnail_url": record.get("thumbnail_url", ""),
            "objects_summary": summary,
            "plate_results": summary.get("plates_detected", []),
            "movement_events_count": summary.get("movement_events", 0),
            "incident_count": record.get("incident_count", 0),
            "camera": record.get("camera_id", "front"),
            "device_id": record.get("device_id", "roadwatch_local_01"),
            "upload_status": "uploaded",
            "visibility": record.get("visibility", "shared"),
        }

    def create_or_update_video_document(self, record, supabase_url="", supabase_path=""):
        document = self.build_video_document(record, supabase_url, supabase_path)
        db = self._client()
        if db:
            db.collection(self.video_collection).document(document["video_id"]).set(document, merge=True)
            return {
                "configured": True,
                "mode": "firestore",
                "document_id": document["video_id"],
                "collection": self.video_collection,
                "document": document,
            }
        if self.rest_configured:
            return self._rest_set_document(document["video_id"], document)
        if self.push_url:
            return self._push_url_document(document)
        return {
            "configured": False,
            "mode": "local",
            "message": "Firebase sync unavailable. Local mode active.",
            "document": document,
        }

    def create_or_update_supabase_video_object(self, video_object, device_id="roadwatch_cloud"):
        object_name = video_object.get("object_name", "")
        filename = video_object.get("name") or Path(object_name).name
        video_id = (
            object_name.replace("/", "_").replace(".", "_")
            if object_name
            else filename.replace(".", "_")
        )
        size = float(video_object.get("size") or 0)
        document = {
            "video_id": video_id,
            "title": filename,
            "created_at": video_object.get("created_at") or utc_now(),
            "updated_at": video_object.get("updated_at") or utc_now(),
            "source": "roadwatch",
            "status": "processed_uploaded",
            "supabase_bucket": video_object.get("bucket", ""),
            "supabase_path": object_name,
            "supabase_url": video_object.get("public_url", ""),
            "supabase_webm_url": video_object.get("public_url", "") if filename.lower().endswith(".webm") else "",
            "supabase_mp4_url": video_object.get("public_url", "") if filename.lower().endswith(".mp4") else "",
            "playback_source": video_object.get("public_url", ""),
            "playback_format": Path(filename).suffix.lower().lstrip("."),
            "processed": True,
            "compressed": filename.lower().endswith(".mp4"),
            "duration_seconds": 0,
            "file_size_mb": round(size / (1024 * 1024), 2) if size else 0,
            "thumbnail_url": "",
            "objects_summary": {},
            "plate_results": [],
            "movement_events_count": 0,
            "incident_count": 0,
            "camera": "front",
            "device_id": device_id,
            "upload_status": "uploaded",
            "visibility": "shared",
        }
        db = self._client()
        if db:
            db.collection(self.video_collection).document(video_id).set(document, merge=True)
            return {
                "configured": True,
                "mode": "firestore",
                "document_id": video_id,
                "collection": self.video_collection,
                "document": document,
            }
        if self.rest_configured:
            return self._rest_set_document(video_id, document)
        if self.push_url:
            return self._push_url_document(document)
        return {
            "configured": False,
            "mode": "local",
            "message": "Firebase sync unavailable. Local mode active.",
            "document": document,
        }

    def fetch_video_documents(self, limit=50):
        db = self._client()
        if not db:
            if self.rest_configured:
                return self._rest_fetch_documents(limit)
            return []
        snapshots = db.collection(self.video_collection).limit(int(limit)).stream()
        docs = [snapshot.to_dict() for snapshot in snapshots]
        return sorted(
            docs,
            key=lambda item: item.get("created_at", ""),
            reverse=True,
        )

    def retry_failed_metadata_upload(self, record):
        return self.create_or_update_video_document(
            record,
            record.get("supabase_processed_url", ""),
            record.get("supabase_processed_path", ""),
        )

    def push_video_link(self, record, processed_video_url):
        return self.create_or_update_video_document(
            record,
            processed_video_url,
            record.get("supabase_processed_path", ""),
        )

    def _push_url_document(self, document):
        url = self.push_url
        if self.api_key:
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}auth={self.api_key}"
        response = requests.post(url, json=document, timeout=30)
        response.raise_for_status()
        return response.json() if response.content else {
            "configured": True,
            "mode": "push_url",
            "status": "pushed",
            "document": document,
        }

    def _rest_set_document(self, document_id, document):
        url = self._rest_document_url(document_id)
        response = requests.patch(
            url,
            json={"fields": self._to_firestore_fields(document)},
            timeout=30,
        )
        response.raise_for_status()
        return {
            "configured": True,
            "mode": "firestore_rest",
            "document_id": document_id,
            "collection": self.video_collection,
            "document": document,
        }

    def _rest_fetch_documents(self, limit=50):
        url = (
            f"https://firestore.googleapis.com/v1/projects/{self.project_id}"
            f"/databases/(default)/documents/{quote(self.video_collection, safe='')}"
            f"?pageSize={int(limit)}&key={self.api_key}"
        )
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        docs = []
        for item in response.json().get("documents", []):
            docs.append(self._from_firestore_fields(item.get("fields", {})))
        return sorted(
            docs,
            key=lambda item: item.get("created_at", ""),
            reverse=True,
        )

    def _rest_document_url(self, document_id):
        return (
            f"https://firestore.googleapis.com/v1/projects/{self.project_id}"
            f"/databases/(default)/documents/{quote(self.video_collection, safe='')}"
            f"/{quote(str(document_id), safe='')}?key={self.api_key}"
        )

    def _to_firestore_fields(self, payload):
        return {key: self._to_firestore_value(value) for key, value in payload.items()}

    def _to_firestore_value(self, value):
        if isinstance(value, bool):
            return {"booleanValue": value}
        if isinstance(value, int):
            return {"integerValue": value}
        if isinstance(value, float):
            return {"doubleValue": value}
        if isinstance(value, list):
            return {"arrayValue": {"values": [self._to_firestore_value(item) for item in value]}}
        if isinstance(value, dict):
            return {"mapValue": {"fields": self._to_firestore_fields(value)}}
        if value is None:
            return {"nullValue": None}
        return {"stringValue": str(value)}

    def _from_firestore_fields(self, fields):
        return {key: self._from_firestore_value(value) for key, value in fields.items()}

    def _from_firestore_value(self, wrapped):
        if "stringValue" in wrapped:
            return wrapped["stringValue"]
        if "booleanValue" in wrapped:
            return wrapped["booleanValue"]
        if "integerValue" in wrapped:
            return int(wrapped["integerValue"])
        if "doubleValue" in wrapped:
            return float(wrapped["doubleValue"])
        if "arrayValue" in wrapped:
            return [
                self._from_firestore_value(item)
                for item in wrapped.get("arrayValue", {}).get("values", [])
            ]
        if "mapValue" in wrapped:
            return self._from_firestore_fields(wrapped.get("mapValue", {}).get("fields", {}))
        return None

    def _client(self):
        if self._db is not None:
            return self._db
        if not self.admin_configured:
            return None
        try:
            import firebase_admin
            from firebase_admin import credentials, firestore
        except Exception as exc:
            self._firebase_error = f"Firebase Admin SDK unavailable: {exc}"
            return None
        try:
            app_name = f"roadwatch-{self.project_id}"
            try:
                app = firebase_admin.get_app(app_name)
            except ValueError:
                cred = credentials.Certificate(
                    {
                        "type": "service_account",
                        "project_id": self.project_id,
                        "private_key": self.private_key,
                        "client_email": self.client_email,
                        "token_uri": "https://oauth2.googleapis.com/token",
                    }
                )
                app = firebase_admin.initialize_app(cred, name=app_name)
            self._db = firestore.client(app)
            return self._db
        except Exception as exc:
            self._firebase_error = str(exc)
            return None
