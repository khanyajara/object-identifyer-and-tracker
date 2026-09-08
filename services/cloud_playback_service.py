import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from services.supabase_service import SupabaseService


def _as_utc(value):
    if not value:
        return None
    if hasattr(value, "to_datetime"):
        value = value.to_datetime()
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _valid_https_url(value):
    parsed = urlparse(str(value or ""))
    return parsed.scheme == "https" and bool(parsed.netloc)


class CloudPlaybackService:
    def __init__(self, supabase):
        self.supabase = supabase

    def resolve_playback_url(self, video):
        result = {
            "ok": False,
            "url": None,
            "format": None,
            "mime_type": None,
            "source": "supabase",
            "error": None,
        }
        bucket = video.get("supabase_bucket") or self.supabase.bucket
        object_path = video.get("supabase_path") or video.get("supabase_processed_path")
        if not bucket or not object_path:
            result["error"] = "Cloud record is missing its Supabase bucket or object path."
            return result
        self.supabase.bucket = bucket
        cached = video.get("cached_playback_url")
        expires = _as_utc(video.get("playback_url_expires_at"))
        cache_safe = expires and expires > datetime.now(timezone.utc) + timedelta(minutes=5)
        try:
            if cached and _valid_https_url(cached) and (self.supabase.bucket_public or cache_safe):
                url = cached
            else:
                playback = self.supabase.create_playback_url(object_path)
                url = playback["url"]
                result["playback_url_created_at"] = playback.get("created_at")
                result["playback_url_expires_at"] = playback.get("expires_at")
            if not _valid_https_url(url):
                raise RuntimeError("Supabase returned a non-HTTPS playback URL.")
            fmt = (video.get("cloud_format") or str(object_path).rsplit(".", 1)[-1]).lower()
            mime = video.get("mime_type") or {"mp4": "video/mp4", "webm": "video/webm"}.get(fmt)
            if not mime or not str(mime).startswith("video/"):
                raise RuntimeError("Cloud record has an invalid video MIME type.")
            result.update(ok=True, url=url, format=fmt, mime_type=mime)
        except Exception as exc:
            result["error"] = str(exc)
        return result

    def test_playback(self, video):
        resolved = self.resolve_playback_url(video)
        checks = {
            "firestore_record": bool(video.get("video_id")),
            "bucket_present": bool(video.get("supabase_bucket")),
            "object_path_present": bool(video.get("supabase_path") or video.get("supabase_processed_path")),
            "url_generated": resolved["ok"],
            "http_success": False,
            "video_content_type": False,
            "nonzero_file": False,
        }
        if resolved["ok"]:
            try:
                import requests
                response = requests.get(
                    resolved["url"], headers={"Range": "bytes=0-0"}, timeout=30, stream=True
                )
                checks["http_success"] = response.status_code in {200, 206}
                checks["video_content_type"] = response.headers.get("content-type", "").lower().startswith("video/")
                size = response.headers.get("content-range", "").rsplit("/", 1)[-1]
                if not size.isdigit():
                    size = response.headers.get("content-length", "0")
                checks["nonzero_file"] = str(size).isdigit() and int(size) > 0
            except Exception as exc:
                resolved["error"] = str(exc)
        return {**resolved, "checks": checks, "ok": all(checks.values())}


def create_playback_url(bucket, object_path, expires_in=3600):
    service = SupabaseService(
        os.getenv("SUPABASE_URL", ""),
        os.getenv("SUPABASE_SECRET_KEY")
        or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        or os.getenv("SUPABASE_ANON_KEY", ""),
        bucket,
        os.getenv("SUPABASE_VIDEO_BUCKET_PUBLIC", "false").lower() == "true",
        expires_in,
    )
    try:
        return service.create_playback_url(object_path, expires_in)["url"]
    except Exception:
        return None


def resolve_playback_url(video):
    bucket = video.get("supabase_bucket") or os.getenv("SUPABASE_VIDEO_BUCKET", "videos")
    service = SupabaseService(
        os.getenv("SUPABASE_URL", ""),
        os.getenv("SUPABASE_SECRET_KEY")
        or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        or os.getenv("SUPABASE_ANON_KEY", ""),
        bucket,
        os.getenv("SUPABASE_VIDEO_BUCKET_PUBLIC", "false").lower() == "true",
        os.getenv("SUPABASE_SIGNED_URL_EXPIRY_SECONDS", "3600"),
    )
    return CloudPlaybackService(service).resolve_playback_url(video)
