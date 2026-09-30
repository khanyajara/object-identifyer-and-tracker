"""Shared upload transaction for local and browser camera recordings."""
from pathlib import Path, PureWindowsPath
import os

from core.storage_paths import DATA_DIR, cloud_runtime
from core.video_io import validate_video_file
from services.supabase_service import SupabaseService, UploadResult
from services.account_identity import video_owner


def upload_processed_video(local_path: Path, *, recording_id: str, bucket: str) -> UploadResult:
    from services.video_service import VideoService
    owner = video_owner(VideoService().load(recording_id))
    return SupabaseService(os.getenv("SUPABASE_URL", ""), os.getenv("SUPABASE_ANON_KEY", ""), bucket, user_id=owner).upload_processed_video(
        local_path, recording_id=recording_id, bucket=bucket)


def retry_source(record):
    if record.get("processing_error") or record.get("processed_compression_status") != "success":
        raise RuntimeError("Processing and compression must succeed before upload.")
    value = record.get("upload_video_path")
    if not value:
        raise RuntimeError("No completed upload source. Process the recording first.")
    if os.name != "nt" and PureWindowsPath(value).drive:
        raise RuntimeError("This Windows source is unavailable on this server. Restore and reprocess the recording.")
    return validate_video_file(Path(value))


def publish_recording(record, local_path, *, storage, firebase, save):
    """Commit metadata only after verification; keep staging on any failure."""
    verified = False
    try:
        if record.get("processing_error") or record.get("processed_compression_status") != "success":
            raise RuntimeError("Processing and compression must succeed before upload.")
        owner = video_owner(record)
        record.update(user_id=owner, uid=owner)
        storage.user_id = owner
        source = validate_video_file(local_path)
        record.update(supabase_upload_status="uploading", upload_status="uploading", cloud_ready=False)
        save(record)
        # Pass the actual compression output, never derive a filename from an ID.
        result = storage.upload_processed_video(source, recording_id=record["video_id"], bucket=storage.bucket)
        if not result.get("verified"):
            raise RuntimeError("Supabase upload was not verified.")
        verified = True
        record.update(supabase_bucket=result["bucket"], supabase_processed_path=result["object_name"],
                      supabase_object_name=result["object_name"], supabase_path=result["object_name"],
                      supabase_processed_url=result["public_url"], supabase_mp4_url=result["public_url"],
                      playback_source=result["public_url"], playback_format="mp4",
                      file_size_bytes=result["size_bytes"], supabase_upload_status="uploaded",
                      upload_status="metadata_pending", firebase_document_status="pending")
        save(record)
        metadata = firebase.push_video_link(record, result["public_url"])
        if not metadata.get("configured"):
            raise RuntimeError("Object verified, but Firestore metadata is unavailable; local source retained.")
        record.update(upload_status="uploaded", firebase_document_status="synced", cloud_ready=True,
                      sync_status="Synced", sync_error=None, supabase_upload_error=None)
        save(record)
    except Exception as exc:
        record.update(supabase_upload_status="uploaded" if verified else "failed",
                      upload_status="metadata_pending" if verified else "failed",
                      firebase_document_status="failed" if verified else "pending", cloud_ready=False,
                      sync_status="Uploaded; metadata sync failed" if verified else "Sync failed",
                      sync_error=str(exc), supabase_upload_error=None if verified else str(exc))
        save(record)
        raise
    # Only disposable cloud staging may be removed, after Firestore acknowledges.
    # Local files are retained. Cloud retry sources do not survive every restart.
    if cloud_runtime():
        for key in ("original_video_path", "video_path", "processed_video_path", "compressed_original_path", "upload_video_path"):
            value = record.get(key)
            if value:
                path = Path(value).resolve()
                if path.is_relative_to(DATA_DIR.resolve()):
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        pass
    return result
