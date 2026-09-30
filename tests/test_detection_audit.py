"""Regressions found during the offline detection and preview audit."""
from types import SimpleNamespace
from unittest.mock import Mock, patch
import threading
import time

import numpy as np
import pytest

from core.vision_pipeline import VisionPipeline
from core.lazy_vision_pipeline import LazyVisionPipeline
from core.dual_camera import DualCameraManager, CameraConfig
from core.movement import MovementDetector


def vehicle():
    return dict(label='car', confidence=95, box=dict(x=0, y=0, width=20, height=20))


def test_camera_movement_does_not_compare_different_feeds():
    with patch('core.vision_pipeline.ObjectDetector') as detector:
        detector.return_value.detect.return_value = []
        pipeline = VisionPipeline('unused', .9, 416, enable_tracking=False)
        black = np.zeros((40, 40, 3), dtype=np.uint8)
        white = np.full((60, 60, 3), 255, dtype=np.uint8)
        for frame, role in [(black, 'front'), (white, 'rear'), (black, 'front'), (white, 'rear')]:
            _, event = pipeline.process(frame, 1, 20, camera_role=role)
            assert not event['movement_detected']


def test_ocr_has_independent_camera_timers_and_scans_first_frame():
    with patch('core.vision_pipeline.ObjectDetector') as detector, patch('core.vision_pipeline.PlateScanner') as scanner:
        detector.return_value.detect.return_value = [vehicle()]
        scanner.return_value.scan.return_value = []
        pipeline = VisionPipeline('unused', .9, 416, enable_tracking=False, enable_ocr=True)
        frame = np.zeros((40, 40, 3), dtype=np.uint8)
        for role in ['front', 'rear']:
            pipeline.process(frame, 1, 20, media_timestamp_seconds=0, camera_role=role)
            pipeline.process(frame, 2, 20, media_timestamp_seconds=1, camera_role=role)
        assert scanner.return_value.scan.call_count == 2
        pipeline.process(frame, 3, 20, media_timestamp_seconds=6, camera_role='front')
        pipeline.process(frame, 1, 20, media_timestamp_seconds=0, camera_role='front')
        assert scanner.return_value.scan.call_count == 4


def test_resolution_change_resets_movement_baseline():
    movement = MovementDetector()
    movement.detect(np.zeros((10, 10, 3), dtype=np.uint8))
    assert movement.detect(np.full((20, 20, 3), 255, dtype=np.uint8)) == (False, 0.0)


def test_lazy_pipeline_loads_enabled_ocr_and_forks_fresh_state():
    settings = dict(model_name='unused', confidence=.9, yolo_image_size=416,
                    enable_tracking=True, enable_ocr=True, ocr_interval_seconds=5)
    pipeline = LazyVisionPipeline(settings)
    reader = object()
    with patch('core.ocr.load_ocr_reader', return_value=reader), patch('core.vision_pipeline.VisionPipeline') as factory:
        pipeline.process(np.zeros((2, 2, 3), dtype=np.uint8), 1, 20)
        assert factory.call_args.kwargs['ocr_reader'] is reader
    fresh = pipeline.fork_for_recording()
    assert fresh._pipeline is None
    assert fresh.settings == settings and fresh.settings is not pipeline.settings


def test_inference_typeerror_is_reported_without_second_inference():
    manager = DualCameraManager([])
    manager.pipeline = Mock()
    manager.pipeline.process.side_effect = TypeError('internal detector failure')
    _, event = manager._handle_ai_frame('front', np.zeros((2, 2, 3), dtype=np.uint8), 1, 0)
    assert 'internal detector failure' in event['error']
    assert manager.pipeline.process.call_count == 1
    _, both = manager.detection_preview('both')
    assert 'internal detector failure' in both['error']


def test_processed_video_rejects_ai_error_event_and_releases_writer(tmp_path):
    from services import dual_video_processing as module
    capture, writer = Mock(), Mock()
    capture.get.return_value = 20
    capture.read.side_effect = [(True, np.zeros((20, 20, 3), dtype=np.uint8)), (False, None)]
    pipeline = Mock(spec=['process'])
    pipeline.process.return_value = (np.zeros((20, 20, 3), dtype=np.uint8), {'error': 'weights unavailable'})
    with patch.object(module.cv2, 'VideoCapture', return_value=capture), patch.object(module, 'open_video_writer', return_value=(writer, 'fake')):
        with pytest.raises(RuntimeError, match='weights unavailable'):
            module._built_in_pass({'video_path': 'fake.mp4', 'role': 'front'}, pipeline, str(tmp_path / 'out.mp4'), 20, None)
    writer.write.assert_not_called()
    writer.release.assert_called_once()
    capture.release.assert_called_once()


def test_detector_rejects_invalid_input_and_missing_weights():
    from core.detector import ObjectDetector, load_yolo_model
    detector = ObjectDetector('unused', .9, 416, model=Mock())
    for frame in [None, np.zeros((0, 0, 3)), np.zeros((10, 10))]:
        with pytest.raises(ValueError, match='BGR'):
            detector.detect(frame)
    detector.model.predict.assert_not_called()
    with patch('core.detector.YOLO') as yolo, patch('core.detector.Path.is_file', return_value=False):
        with pytest.raises(FileNotFoundError, match='weights are missing'):
            load_yolo_model('missing-audit-model.pt')
        yolo.assert_not_called()


def test_shared_model_serializes_predictions_across_detectors():
    from core.detector import ObjectDetector
    running, overlap, errors = [], [], []
    first_entered, release_first = threading.Event(), threading.Event()
    class Model:
        names = {}
        def predict(self, *args, **kwargs):
            if running:
                overlap.append(True)
            running.append(True)
            first_entered.set()
            release_first.wait(2)
            running.pop()
            return [SimpleNamespace(boxes=[])]
    model = Model()
    detectors = [ObjectDetector('unused', .9, 416, model=model) for _ in range(2)]
    assert detectors[0]._model_lock is detectors[1]._model_lock
    def detect(detector):
        try:
            detector.detect(np.zeros((2, 2, 3), dtype=np.uint8))
        except Exception as exc:
            errors.append(exc)
    threads = [threading.Thread(target=detect, args=(detector,)) for detector in detectors]
    threads[0].start()
    assert first_entered.wait(2)
    threads[1].start()
    release_first.set()
    for thread in threads:
        thread.join(3)
        assert not thread.is_alive()
    assert not overlap and not errors


def test_smooth_preview_drops_boxes_after_resolution_change():
    manager = DualCameraManager([CameraConfig(0)])
    event = {'camera_role': 'front', 'objects': [vehicle()]}
    manager._preview_results['front'] = (np.zeros((40, 40, 3), dtype=np.uint8), event, time.monotonic())
    with patch.object(manager.channel('front'), 'get_preview', return_value=np.zeros((20, 20, 3), dtype=np.uint8)):
        _, result = manager.smooth_detection_preview('front')
    assert result['objects'] == []


def test_smooth_preview_is_opt_in_and_retained():
    import streamlit_app
    manager = streamlit_app.DualCameraUIManager.__new__(streamlit_app.DualCameraUIManager)
    manager.manager = Mock()
    manager.manager.detection_preview.return_value = (None, {})
    manager.manager.smooth_detection_preview.return_value = (None, {})
    manager.manager.status.return_value = {}
    manager.get_dashboard_state()
    manager.manager.smooth_detection_preview.assert_not_called()
    manager.smooth_preview = True
    manager.get_dashboard_state()
    manager.manager.smooth_detection_preview.assert_called_once()

def test_dual_summary_counts_tracks_instead_of_frame_occurrences():
    from services.dual_session_service import build_video_metadata
    events = [dict(objects=[{**vehicle(), 'tracking_id': 1}], people_count=0, vehicle_count=1,
                   movement_detected=True, plates=[{'text': 'ABC123'}]) for _ in range(5)]
    record = build_video_metadata({'user_id': 'owner'}, {'video_id': 'v', 'role': 'front'}, events)
    assert record['objects_summary']['object_counts'] == {'car': 1}
    assert record['objects_summary']['unique_tracking_ids'] == [1]
    assert record['objects_summary']['plates_detected'] == ['ABC123']
    assert record['objects_summary']['movement_events'] == 1
    assert record['user_id'] == 'owner'


def test_same_second_recordings_have_different_file_paths(tmp_path, monkeypatch):
    from services import video_service
    monkeypatch.setattr(video_service, 'VIDEOS_DIR', tmp_path)
    service = video_service.VideoService.__new__(video_service.VideoService)
    service.save = Mock()
    with patch('services.driver_monitoring.runtime.recording_context'):
        first = service.create_record('camera', 20, '640x480', user_id='same-user')
        second = service.create_record('camera', 20, '640x480', user_id='same-user')
    assert first['video_path'] != second['video_path']


def test_video_player_denies_foreign_record_before_fetching_url():
    import streamlit_app
    with patch.object(streamlit_app, 'account_videos', return_value=[]), patch.object(streamlit_app, 'fetch_cloud_playback_url') as fetch, patch.object(streamlit_app.st, 'warning') as warning:
        streamlit_app.render_video_player({'video_id': 'other-user'})
    fetch.assert_not_called()
    warning.assert_called_once()


def test_processing_writer_setup_failure_releases_capture():
    from services import dual_video_processing as module
    capture = Mock()
    capture.get.return_value = 20
    with patch.object(module.cv2, 'VideoCapture', return_value=capture), patch.object(module, 'open_video_writer', side_effect=RuntimeError('codec failed')):
        with pytest.raises(RuntimeError, match='codec failed'):
            module._built_in_pass({'video_path': 'fake', 'role': 'front'}, Mock(), 'out.mp4', 20, None)
    capture.release.assert_called_once()
