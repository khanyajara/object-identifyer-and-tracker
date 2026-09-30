from unittest.mock import Mock, patch

import pytest

from services.account_identity import account_uid, can_access_video, video_owner
from services.dual_session_service import build_video_metadata
from services.firebase_service import FirebaseService
from services.recording_upload_service import publish_recording
from services.supabase_service import SupabaseService


def test_account_uid_is_stable_and_preserves_existing_uid():
    assert account_uid({'username': 'Alice'}) == account_uid({'username': 'alice'})
    assert account_uid({'username': 'alice'}) != account_uid({'username': 'bob'})
    assert account_uid({'username': 'alice', 'uid': 'provider-123'}) == 'provider-123'


def test_owner_survives_dual_processing_and_firestore():
    record = build_video_metadata({'user_id': 'owner-at-start', 'uid': 'owner-at-start'}, {'video_id': 'vid-test'})
    assert record['user_id'] == record['uid'] == 'owner-at-start'
    doc = FirebaseService().build_video_document(record)
    assert doc['user_id'] == doc['uid'] == 'owner-at-start'


def test_upload_stored_in_owners_namespace(tmp_path):
    path = tmp_path / 'clip.mp4'
    path.write_bytes(b'video')
    storage = SupabaseService('https://example.test', 'fake', bucket_public=True, user_id='owner-a')
    with patch('services.supabase_service.requests.post'), patch.object(storage, 'list_objects', return_value=[{'name': 'clip.mp4', 'metadata': {'size': 5}}]) as listing:
        result = storage.upload_processed_video(path, recording_id='vid-test', bucket='videos')
    assert result['object_name'] == 'users/owner-a/processed/vid-test/clip.mp4'
    listing.assert_called_once_with(prefix='users/owner-a/processed/vid-test')


def test_upload_ownerless_legacy_record_is_not_assigned_to_uploader(tmp_path):
    path = tmp_path / 'clip.mp4'
    path.write_bytes(b'video')
    storage = Mock(bucket='videos', user_id='currently-signed-in-user')
    record = {'video_id': 'old', 'processed_compression_status': 'success'}
    with pytest.raises(ValueError, match='owner is missing'):
        publish_recording(record, path, storage=storage, firebase=Mock(), save=lambda _: None)
    storage.upload_processed_video.assert_not_called()
    assert path.exists()


def test_user_video_access_and_admin_access():
    record = {'user_id': 'alice-uid'}
    assert can_access_video(record, {'uid': 'alice-uid', 'role': 'user'})
    assert not can_access_video(record, {'uid': 'bob-uid', 'role': 'user'})
    assert not can_access_video({}, {'uid': 'alice-uid', 'role': 'user'})
    assert can_access_video(record, {'role': 'admin'})
    with pytest.raises(ValueError, match='disagree'):
        video_owner({'uid': 'alice-uid', 'user_id': 'bob-uid'})

def test_local_record_uses_account_directory(tmp_path, monkeypatch):
    from services import video_service
    monkeypatch.setattr(video_service, 'VIDEOS_DIR', tmp_path)
    service = video_service.VideoService.__new__(video_service.VideoService)
    service.save = Mock()
    with patch('services.driver_monitoring.runtime.recording_context'):
        record = service.create_record('camera', 20, '640x480', user_id='alice-uid')
    assert record['user_id'] == record['uid'] == 'alice-uid'
    from pathlib import Path
    assert Path(record['video_path']).parent == tmp_path / 'users' / 'alice-uid'


def test_video_api_filters_owner_and_denies_other_users():
    import api_app
    from fastapi import HTTPException
    alice = {'uid': 'alice-uid', 'role': 'user'}
    owned = {'video_id': 'a', 'user_id': 'alice-uid'}
    other = {'video_id': 'b', 'user_id': 'bob-uid'}
    with patch.object(api_app.service, 'list_videos', return_value=[owned, other]):
        assert api_app.videos(alice) == [owned]
    with patch.object(api_app.service, 'load', return_value=other):
        with pytest.raises(HTTPException) as error:
            api_app.video('b', alice)
        assert error.value.status_code == 404


def test_storage_inventory_preserves_owner_for_firestore_reconciliation():
    storage = SupabaseService('https://example.test', 'fake', bucket_public=True)
    with patch.object(storage, 'list_objects', return_value=[{'name': 'clip.mp4', 'metadata': {'size': 5}}]):
        objects = storage.list_video_objects('users/alice-uid/processed/vid-test')
    assert objects[0]['user_id'] == 'alice-uid'
