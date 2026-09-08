import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from services.cloud_playback_service import CloudPlaybackService
from services.firebase_video_service import normalize_video
from services.supabase_service import SupabaseService


class CloudPlaybackTests(unittest.TestCase):
    def test_valid_cached_signed_url_is_reused(self):
        supabase = Mock(bucket="videos", bucket_public=False)
        result = CloudPlaybackService(supabase).resolve_playback_url(
            {
                "supabase_bucket": "videos",
                "supabase_path": "processed/device/video.mp4",
                "cached_playback_url": "https://example.supabase.co/signed/video.mp4?token=x",
                "playback_url_expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                "cloud_format": "mp4",
                "mime_type": "video/mp4",
            }
        )
        self.assertTrue(result["ok"])
        supabase.create_playback_url.assert_not_called()

    def test_expired_url_is_replaced_with_fresh_signed_url(self):
        supabase = Mock(bucket="videos", bucket_public=False)
        supabase.create_playback_url.return_value = {
            "url": "https://example.supabase.co/fresh.mp4?token=new",
            "created_at": "2026-08-05T00:00:00+00:00",
            "expires_at": "2026-08-05T01:00:00+00:00",
        }
        result = CloudPlaybackService(supabase).resolve_playback_url(
            {
                "supabase_bucket": "videos",
                "supabase_path": "processed/device/video.mp4",
                "cached_playback_url": "https://example.supabase.co/old.mp4?token=old",
                "playback_url_expires_at": "2020-01-01T00:00:00+00:00",
                "mime_type": "video/mp4",
            }
        )
        self.assertTrue(result["ok"])
        self.assertIn("fresh.mp4", result["url"])

    def test_local_path_is_never_returned(self):
        supabase = Mock(bucket="videos", bucket_public=False)
        supabase.create_playback_url.return_value = {"url": r"C:\\videos\\clip.mp4"}
        result = CloudPlaybackService(supabase).resolve_playback_url(
            {"supabase_bucket": "videos", "supabase_path": "processed/clip.mp4", "mime_type": "video/mp4"}
        )
        self.assertFalse(result["ok"])

    @patch("services.supabase_service.requests.post")
    def test_private_bucket_uses_signed_url_endpoint(self, post):
        post.return_value.json.return_value = {"signedURL": "/object/sign/videos/clip.mp4?token=x"}
        post.return_value.raise_for_status.return_value = None
        result = SupabaseService("https://project.supabase.co", "key", "videos", False).create_playback_url("clip.mp4")
        self.assertIn("/storage/v1/object/sign/", result["url"])

    def test_normalization_promotes_stable_cloud_identifiers(self):
        item = normalize_video(
            {"supabase_processed_path": "processed/device/clip.mp4", "upload_status": "uploaded", "file_size_mb": 1},
            "vid_1",
        )
        self.assertEqual(item["supabase_path"], "processed/device/clip.mp4")
        self.assertEqual(item["mime_type"], "video/mp4")
        self.assertTrue(item["cloud_ready"])
        self.assertEqual(item["file_size_bytes"], 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
