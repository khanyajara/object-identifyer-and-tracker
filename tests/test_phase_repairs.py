"""Integration checks for repaired boundaries, without cameras or cloud traffic."""
from pathlib import Path
from unittest.mock import Mock, patch

import pytest


@pytest.mark.parametrize("metadata_configured", [True, False])
def test_dashboard_upload_verifies_exact_source_before_metadata(tmp_path, metadata_configured):
    import streamlit_app as app
    path = tmp_path / "actual.mp4"
    path.write_bytes(b"completed-video")
    record = {"video_id": "vid-test", "user_id": "owner-a", "uid": "owner-a",
              "upload_video_path": str(path), "processed_compression_status": "success"}
    service = Mock()
    service.load.return_value = record
    storage = Mock()
    storage.upload_processed_video.return_value = {
        "bucket": "videos", "object_name": "users/owner-a/processed/vid-test/actual.mp4",
        "public_url": "https://example.test/video", "mp4_url": "https://example.test/video",
        "verified": True, "size_bytes": path.stat().st_size,
    }
    firebase = Mock()
    firebase.push_video_link.return_value = {"configured": metadata_configured}
    settings = {"notify_on_sync": False, "supabase_bucket": "videos"}
    with patch.object(app, "VideoService", return_value=service), \
         patch.object(app, "SupabaseService", return_value=storage) as factory, \
         patch.object(app, "firebase_service_from_settings", return_value=firebase), \
         patch("services.supabase_database_service.SupabaseDatabaseService") as database:
        database.return_value.enabled = False
        app.upload_processed_video_background("vid-test", settings)
    assert factory.call_args.kwargs["user_id"] == "owner-a"
    storage.upload_processed_video.assert_called_once_with(path, recording_id="vid-test", bucket="videos")
    firebase.push_video_link.assert_called_once()
    final = service.update_sync_fields.call_args.kwargs
    assert final["cloud_ready"] is metadata_configured
    assert final["upload_status"] == ("uploaded" if metadata_configured else "metadata_pending")
    assert path.exists()


def test_dual_video_ids_are_readable_without_allowing_path_traversal():
    from services.video_service import VideoService
    VideoService._validate_video_id("vid_20261006_120000_front_abcdef")
    VideoService._validate_video_id("vid_20261006_120000_rear_abcdef")
    with pytest.raises(ValueError):
        VideoService._validate_video_id("vid_20261006_120000_front_abcdef/../settings")


def test_finalizer_returns_completed_path(tmp_path):
    from core.video_io import finalize_video_file
    source, target = tmp_path / "raw.mp4", tmp_path / "complete.mp4"
    source.write_bytes(b"video")
    assert finalize_video_file(source, target) == target
    assert target.read_bytes() == b"video"


def test_account_owner_is_not_written_to_shared_settings(tmp_path):
    import json
    import streamlit_app as app
    path = tmp_path / "settings.json"
    with patch.object(app, "SETTINGS_PATH", path):
        app.save_settings({"recording_user_id": "session-owner", "confidence": .9})
    assert "recording_user_id" not in json.loads(path.read_text())


def test_signout_finalizes_dual_recording_before_releasing_preview():
    import streamlit_app as app
    manager = app.DualCameraUIManager.__new__(app.DualCameraUIManager)
    manager.manager = Mock()
    manager.manager.is_recording = True
    manager.session = {"session_id": "session", "user_id": "owner-at-start"}
    manager.record = {}
    manager.manager.stop_recording.return_value = manager.session
    state = type("State", (dict,), {"__setattr__": dict.__setitem__})(camera_manager=manager)
    with patch.object(app.st, "session_state", state), \
         patch.object(app.dual_session_service, "save_session") as save:
        app.close_account_capture()
    save.assert_called_once_with(manager.session)
    manager.manager.close_preview.assert_called_once()
    assert state["camera_manager"] is None


def test_runtime_services_share_local_or_cloud_storage():
    import os
    import subprocess
    import sys
    code = '''from core.storage_paths import DATA_DIR
from services.video_service import DATA_DIR as media
from services.local_json_service import DATA_DIR as records
from services.supabase_sync_service import DATA_ROOT as sync
from services.dual_session_service import LOGS_DIR
from services.dual_video_processing import PROCESSED_DIR
from core.dual_camera import DualCameraManager
from pathlib import Path
assert DATA_DIR == media == records == sync
assert Path(LOGS_DIR) == DATA_DIR / 'logs'
assert Path(PROCESSED_DIR) == DATA_DIR / 'videos' / 'processed'
assert Path(DualCameraManager([]).video_dir) == DATA_DIR / 'videos'
'''
    for mode in ("local", "temporary"):
        result = subprocess.run([sys.executable, "-c", code], env={**os.environ, "ROADWATCH_STORAGE_MODE": mode},
                                capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stderr


def test_sync_persists_metadata_once_and_ignores_supplied_paths(tmp_path, monkeypatch):
    from services import video_service as module
    monkeypatch.setattr(module, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(module, "VIDEOS_DIR", tmp_path / "videos")
    service = module.VideoService.__new__(module.VideoService)
    payload = {"video_id": "vid_20261006_120000_abcdef", "user_id": "owner", "uid": "owner",
               "video_path": str(tmp_path / "outside.mp4"), "password": "do-not-store",
               "detections": [{"people_count": 1, "objects": [{"label": "person", "tracking_id": 1}]}]}
    first = service.ingest_metadata(payload)
    second = service.ingest_metadata(payload)
    assert first["video_path"] == second["video_path"]
    assert Path(first["video_path"]).parent == tmp_path / "videos" / "users" / "owner"
    assert len(service.list_videos()) == 1
    assert service.load(payload["video_id"])["objects_summary"]["object_counts"] == {"person": 1}
    assert "password" not in first
    with pytest.raises(ValueError, match="owner cannot be changed"):
        service.ingest_metadata({**payload, "user_id": "another", "uid": "another"})
    assert service.load(payload["video_id"])["user_id"] == "owner"


def test_sync_rejects_invalid_data_before_writing(tmp_path, monkeypatch):
    from services import video_service as module
    monkeypatch.setattr(module, "LOGS_DIR", tmp_path / "logs")
    service = module.VideoService.__new__(module.VideoService)
    for payload in ({"video_id": "../bad", "user_id": "owner"},
                    {"video_id": "vid_20261006_120000_abcdef", "user_id": "../owner"},
                    {"video_id": "vid_20261006_120000_abcdef", "user_id": "owner", "detections": [None]}):
        with pytest.raises(ValueError):
            service.ingest_metadata(payload)
    assert not (tmp_path / "logs").exists()


def test_dual_metadata_has_library_timestamp_and_nullable_duration():
    from services.dual_session_service import build_video_metadata
    from services.report_service import videos_dataframe
    record = build_video_metadata({"started_at": "2026-10-06T12:00:00+02:00"},
                                  {"video_id": "clip", "duration_seconds": None})
    assert record["started_at"] == "2026-10-06T12:00:00+02:00"
    assert videos_dataframe([record]).iloc[0]["Duration (seconds)"] == 0
    assert videos_dataframe([{"detections": None, "objects_summary": None, "duration_seconds": None}]).iloc[0]["Detection events"] == 0


def test_supabase_rest_endpoint_normalizes_to_project_origin(monkeypatch):
    from services.supabase_service import SupabaseService
    from services.supabase_database_service import SupabaseDatabaseService
    monkeypatch.setenv("SUPABASE_URL", "https://example.test/rest/v1/")
    assert SupabaseService("https://example.test/rest/v1/", "key").url == "https://example.test"
    assert SupabaseDatabaseService().url == "https://example.test"


def test_concurrent_sync_status_updates_preserve_all_fields(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from services import video_service as module
    monkeypatch.setattr(module, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(module, "VIDEOS_DIR", tmp_path / "videos")
    service = module.VideoService.__new__(module.VideoService)
    record = service.ingest_metadata({"video_id": "vid_20261006_120000_abcdef", "user_id": "owner"})
    with ThreadPoolExecutor(max_workers=6) as workers:
        list(workers.map(lambda i: service.update_sync_fields(record["video_id"], **{"worker_" + str(i): i}), range(12)))
    saved = service.load(record["video_id"])
    assert all(saved["worker_" + str(i)] == i for i in range(12))
    import json
    assert json.loads(Path(saved["video_path"]).with_suffix(".json").read_text()) == saved


def test_single_processing_releases_unreadable_capture(tmp_path):
    from services import video_processing_service as module
    path = tmp_path / "broken.mp4"
    path.write_bytes(b"unreadable")
    service = module.VideoProcessingService.__new__(module.VideoProcessingService)
    capture = Mock()
    capture.isOpened.return_value = False
    with patch.object(module.cv2, "VideoCapture", return_value=capture):
        with pytest.raises(RuntimeError, match="Could not open"):
            service._process_video({}, path, tmp_path / "processed.mp4")
    capture.release.assert_called_once()
