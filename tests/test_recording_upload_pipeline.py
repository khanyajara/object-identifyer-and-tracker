"""Offline contracts for the recording -> cloud transaction."""
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from core.video_io import validate_video_file
from services.recording_upload_service import publish_recording, retry_source
from services.supabase_service import SupabaseService


@pytest.fixture
def media(tmp_path):
    path = tmp_path / 'actual codec output.mp4'
    path.write_bytes(b'finished-video')
    return path


def record_for(path):
    return dict(video_id='vid-test', user_id='owner-a', uid='owner-a', upload_video_path=str(path), processed_video_path=str(path),
                processed_compression_status='success')


def storage_for(path):
    storage = Mock(bucket='videos')
    storage.upload_processed_video.return_value = dict(bucket='videos', object_name='processed/vid-test/actual.mp4',
        public_url='https://example.test/video', verified=True, size_bytes=path.stat().st_size)
    return storage


@pytest.mark.parametrize('contents', [None, b''])
def test_upload_missing_empty_path(tmp_path, contents):
    path = tmp_path / 'missing.mp4'
    if contents is not None:
        path.write_bytes(contents)
    with pytest.raises(RuntimeError, match='missing|empty'):
        validate_video_file(path)


def test_upload_success_verifies_exact_object_size(media):
    service = SupabaseService('https://example.test', 'key', bucket_public=True)
    with patch('services.supabase_service.requests.post') as post, patch.object(service, 'list_objects', return_value=[
        {'name': media.name, 'metadata': {'size': media.stat().st_size}}]):
        result = service.upload_processed_video(media, recording_id='vid-test', bucket='videos')
    assert result['verified']
    assert result['object_name'] == 'processed/vid-test/' + media.name
    assert post.call_args.kwargs['data'].closed


@pytest.mark.parametrize('failure', ['upload', 'verification'])
def test_upload_failure_retains_source(media, failure):
    service = SupabaseService('https://example.test', 'key', bucket_public=True)
    with patch('services.supabase_service.requests.post') as post, patch.object(service, 'list_objects', return_value=[]):
        post.return_value.ok = failure != 'upload'
        post.return_value.text = 'denied'
        with pytest.raises(RuntimeError):
            service.upload_processed_video(media, recording_id='vid-test', bucket='videos')
    assert media.exists()


@pytest.mark.parametrize('failure', [None, 'upload', 'metadata', 'unconfigured'])
def test_status_order_and_source_retention(media, failure):
    record = record_for(media)
    storage = storage_for(media)
    firebase = Mock()
    firebase.push_video_link.return_value = {'configured': failure != 'unconfigured'}
    if failure == 'upload':
        storage.upload_processed_video.side_effect = RuntimeError('upload failed')
    if failure == 'metadata':
        firebase.push_video_link.side_effect = RuntimeError('metadata failed')
    saved = []
    def save(item):
        saved.append(deepcopy(item))
    with patch('services.recording_upload_service.cloud_runtime', return_value=False):
        if failure:
            with pytest.raises(RuntimeError):
                publish_recording(record, media, storage=storage, firebase=firebase, save=save)
        else:
            publish_recording(record, media, storage=storage, firebase=firebase, save=save)
    assert media.exists()
    assert saved[0]['upload_status'] == 'uploading'
    assert record['upload_status'] == ('uploaded' if failure is None else 'failed' if failure == 'upload' else 'metadata_pending')
    if failure == 'upload':
        firebase.push_video_link.assert_not_called()
    else:
        assert saved[1]['upload_status'] == 'metadata_pending'
    if not failure:
        storage.upload_processed_video.assert_called_once_with(media, recording_id='vid-test', bucket='videos')


def test_status_cloud_cleanup_only_after_metadata(media):
    record = record_for(media)
    firebase = Mock()
    def push(*args):
        assert media.exists()
        return {'configured': True}
    firebase.push_video_link.side_effect = push
    with patch('services.recording_upload_service.cloud_runtime', return_value=True), patch('services.recording_upload_service.DATA_DIR', media.parent):
        publish_recording(record, media, storage=storage_for(media), firebase=firebase, save=lambda _: None)
    assert not media.exists()


@pytest.mark.parametrize('field,value', [('processing_error', 'detector failed'), ('processed_compression_status', 'failed')])
def test_processing_compression_failure_blocks_upload(media, field, value):
    record = record_for(media)
    record[field] = value
    storage = storage_for(media)
    with pytest.raises(RuntimeError):
        publish_recording(record, media, storage=storage, firebase=Mock(), save=lambda _: None)
    storage.upload_processed_video.assert_not_called()
    assert record['upload_status'] == 'failed'
    assert media.exists()


def test_linux_rejects_stale_windows_path():
    with patch('services.recording_upload_service.os.name', 'posix'):
        with pytest.raises(RuntimeError, match='Windows source'):
            retry_source(record_for(r'C:\old-machine\recording.mp4'))


def test_native_path_retry_uses_exact_existing_file(media):
    assert retry_source(record_for(media)) == media
    media.unlink()
    with pytest.raises(RuntimeError, match='Retry requires'):
        retry_source(record_for(media))


def test_storage_path_local_and_cloud():
    from core.storage_paths import data_directory, PROJECT_DIR
    with patch('core.storage_paths.cloud_runtime', return_value=False):
        assert data_directory() == PROJECT_DIR / 'data'
    with patch('core.storage_paths.cloud_runtime', return_value=True), patch('core.storage_paths.tempfile.gettempdir', return_value='/tmp'):
        assert data_directory() == Path('/tmp') / 'roadwatch' / 'data'


def test_firestore_status_document_has_no_local_playback(media):
    from services.firebase_service import FirebaseService
    record = record_for(media)
    record.update(playback_source=r'C:\stale\video.mp4', supabase_bucket='videos', supabase_processed_path='processed/vid-test/a.mp4')
    doc = FirebaseService().build_video_document(record, 'https://example.test/a.mp4')
    assert doc['playback_source'] == 'https://example.test/a.mp4'
    assert doc['supabase_path'] == 'processed/vid-test/a.mp4'
    assert doc['processing_status'] == 'completed'
    assert 'upload_video_path' not in doc


def test_processing_returns_actual_compression_path_after_release(media, monkeypatch):
    from services import video_processing_service as module
    service = module.VideoProcessingService.__new__(module.VideoProcessingService)
    service.video_service = Mock()
    service._replace_detection_metadata = Mock()
    actual = media.parent / 'unexpected.mp4'
    actual.write_bytes(b'compressed')
    monkeypatch.setattr(module, 'PROCESSED_VIDEOS_DIR', media.parent)
    monkeypatch.setattr(module, 'COMPRESSED_PROCESSED_VIDEOS_DIR', media.parent)
    service._process_video = Mock(return_value=([], 1, media))
    compressor = Mock()
    compressor.compress_for_playback.return_value = {'ok': True, 'path': actual, 'message': 'done'}
    compressor.create_thumbnail.return_value = {'ok': False, 'error': 'no thumbnail'}
    monkeypatch.setattr(module, 'CompressionService', lambda: compressor)
    record = record_for(media)
    record['original_video_path'] = str(media)
    assert service.process(record) == actual
    assert record['upload_video_path'] == str(actual)


@pytest.mark.parametrize('failure', ['processing', 'compression'])
def test_processing_compression_error_clears_stale_path(media, monkeypatch, failure):
    from services import video_processing_service as module
    service = module.VideoProcessingService.__new__(module.VideoProcessingService)
    service.video_service = Mock()
    service._process_video = Mock(return_value=([], 1, media))
    if failure == 'processing':
        service._process_video.side_effect = RuntimeError('processing failed')
    monkeypatch.setattr(module, 'PROCESSED_VIDEOS_DIR', media.parent)
    compressor = Mock()
    compressor.compress_for_playback.return_value = {'ok': False, 'error': 'encoder failed'}
    monkeypatch.setattr(module, 'CompressionService', lambda: compressor)
    record = record_for(media)
    record['original_video_path'] = str(media)
    assert service.process(record) is None
    assert record['upload_video_path'] is None
    assert record['processing_error']

def test_writer_released_before_processing_output_is_returned(media, monkeypatch):
    from services import video_processing_service as module
    service = module.VideoProcessingService.__new__(module.VideoProcessingService)
    service.settings = dict(target_camera_fps=20, model_name='fake', confidence=.5,
                            yolo_image_size=320, enable_ocr=False, ocr_interval_seconds=2)
    service.model = service.ocr_reader = None
    capture = Mock()
    capture.get.return_value = 20
    capture.read.side_effect = [(True, object()), (False, None)]
    writer = Mock()
    monkeypatch.setattr(module.cv2, 'VideoCapture', lambda _: capture)
    monkeypatch.setattr(module, 'open_video_writer', lambda *args: (writer, media, 'fake'))
    pipeline = Mock()
    pipeline.process.return_value = (object(), {})
    monkeypatch.setattr(module, 'VisionPipeline', lambda *args: pipeline)
    _, count, output = service._process_video({'video_id': 'test'}, media, media)
    assert count == 1 and output == media
    writer.release.assert_called_once()
    capture.release.assert_called_once()


def test_dual_compression_exception_retains_source_and_blocks_upload(media):
    from services import dual_video_processing as module
    with patch.object(module, '_video_is_readable', return_value=True), patch.object(module, 'processed_path_for', return_value=str(media)), patch.object(module, '_built_in_pass', return_value=(True, [], None)), patch('services.compression_service.CompressionService.compress_for_playback', side_effect=RuntimeError('encoder failed')):
        result = module.process_camera({}, dict(video_path=str(media), role='rear'))
    assert result['upload_video_path'] is None
    assert result['processed_compression_status'] == 'failed'
    assert 'encoder failed' in result['processing_error']
    assert media.exists()
