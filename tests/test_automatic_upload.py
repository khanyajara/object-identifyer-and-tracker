from unittest.mock import Mock, patch
from types import SimpleNamespace

import streamlit_app as app


def test_stopping_dual_recording_queues_both_saved_camera_videos():
    session = {"session_id": "s", "primary_video_id": "front", "processing_status": "Processed",
               "cameras": [{"video_id": "front", "processed_video_path": "front.mp4"},
                           {"video_id": "rear", "processed_video_path": "rear.mp4"}]}
    manager = Mock(record={}, session={}, pipeline=Mock())
    service = Mock()
    service.load.side_effect = [{"video_id": "front"}, {"video_id": "rear"}]
    with patch.object(app.st, "status"), patch.object(app.st, "session_state", SimpleNamespace()), \
         patch.object(app, "unlink_location_from_video"), patch.object(app, "VideoService", return_value=service), \
         patch.object(app.dual_video_processing, "process_session", return_value=session), \
         patch.object(app, "auto_upload_completed_video") as upload:
        app.stop_and_process_dual(manager, {"notify_on_processing": False})
    assert [call.args[0]["video_id"] for call in upload.call_args_list] == ["front", "rear"]
    manager.stop.assert_called_once()


def test_completed_compression_automatically_queues_upload():
    record = {"video_id": "v", "processed_compression_status": "success"}
    with patch.object(app, "queue_processed_video_upload", return_value={"task_id": "t"}) as queue:
        assert app.auto_upload_completed_video(record, {}) == {"task_id": "t"}
    queue.assert_called_once_with(record, {})


def test_failed_processing_or_already_uploaded_does_not_queue():
    with patch.object(app, "queue_processed_video_upload") as queue:
        for record in ({}, {"processing_error": "failed"},
                       {"processed_compression_status": "failed"},
                       {"processed_compression_status": "success", "supabase_upload_status": "uploaded"}):
            assert app.auto_upload_completed_video(record, {}) is None
    queue.assert_not_called()


def test_repeated_queue_request_reuses_active_task():
    task = {"video_id": "v", "task_id": "existing"}
    queue = Mock()
    queue.active_tasks.return_value = [task]
    with patch.object(app, "UploadQueueService", return_value=queue), patch.object(app, "_queue_processed_video_upload") as start:
        assert app.queue_processed_video_upload({"video_id": "v"}, {}) == task
    start.assert_not_called()


def test_configuration_failure_is_saved_for_retry():
    record = {"video_id": "v", "processed_compression_status": "success"}
    service = Mock()
    with patch.object(app, "queue_processed_video_upload", side_effect=RuntimeError("Storage unavailable")), patch.object(app, "VideoService", return_value=service):
        assert app.auto_upload_completed_video(record, {}) is None
    assert service.update_sync_fields.call_args.kwargs["upload_status"] == "failed"


def test_expired_playback_url_is_refreshed_and_saved():
    record = {"video_id": "v", "supabase_processed_path": "processed/v/a.mp4",
              "supabase_processed_url": "https://example.test/expired",
              "playback_url_expires_at": "2000-01-01T00:00:00Z"}
    storage = Mock()
    storage.refresh_video_url.return_value = {"public_url": "https://example.test/fresh", "expires_at": "2099-01-01T00:00:00Z"}
    service = Mock()
    service.update_sync_fields.return_value = {}
    with patch.object(app, "SupabaseService", return_value=storage), patch.object(app, "VideoService", return_value=service):
        assert app.fetch_cloud_playback_url(record, {"supabase_url": "https://example.test"}) == "https://example.test/fresh"
    storage.refresh_video_url.assert_called_once()
    assert service.update_sync_fields.call_args.kwargs["playback_url_expires_at"] == "2099-01-01T00:00:00Z"
