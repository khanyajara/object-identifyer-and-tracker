"""Offline smoke check using bundled Ultralytics images and local weights."""
import json
from pathlib import Path
import sys
import time
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    import cv2
    import ultralytics
    from core.vision_pipeline import VisionPipeline
    from core.ocr import load_ocr_reader
    settings = json.loads((ROOT / 'settings.json').read_text())
    results = []
    # Any accidental model download must fail rather than hide a missing asset.
    with patch('socket.socket.connect', side_effect=RuntimeError('Offline check: network disabled')):
        pipeline = VisionPipeline(settings['model_name'], settings['confidence'], settings['yolo_image_size'],
                                  True, settings['enable_ocr'], settings['ocr_interval_seconds'],
                                  ocr_reader=load_ocr_reader() if settings['enable_ocr'] else None)
        assets = Path(ultralytics.__file__).parent / 'assets'
        for index, name in enumerate(('bus.jpg', 'zidane.jpg')):
            pipeline.movement.previous = None
            frame = cv2.imread(str(assets / name))
            if frame is None:
                raise RuntimeError(f'Bundled smoke image missing: {name}')
            started = time.perf_counter()
            annotated, event = pipeline.process(frame, 1, 0, media_timestamp_seconds=0, camera_role=name)
            first_ids = [item['tracking_id'] for item in event['objects']]
            _, repeated = pipeline.process(frame, 2, 0, media_timestamp_seconds=.1, camera_role=name)
            results.append(dict(image=name, seconds=round(time.perf_counter()-started, 3),
                                objects=[{k: item[k] for k in ('label', 'confidence', 'tracking_id')} for item in event['objects']],
                                repeated_ids=[item['tracking_id'] for item in repeated['objects']],
                                initial_ids=first_ids, movement=event['movement_detected']))
        from core.video_io import open_video_writer, finalize_video_file, is_playable_video_path
        from services.compression_service import CompressionService
        with tempfile.TemporaryDirectory(prefix="roadwatch-vision-audit-") as directory:
            directory = Path(directory)
            writer, actual, codec = open_video_writer(directory / 'capture.raw.mp4', 10, (640, 480))
            try:
                for _ in range(6):
                    writer.write(cv2.resize(annotated, (640, 480)))
            finally:
                writer.release()
            finished = directory / 'finished.mp4'
            finalize_video_file(actual, finished)
            compression = CompressionService().compress_for_playback(finished, directory / 'compressed' / 'actual.mp4')
            if not compression['ok']:
                raise RuntimeError(compression.get('error') or 'Compression failed')
            roundtrip = dict(writer_codec=codec, compression_tool=compression.get('tool'),
                             output_bytes=Path(compression['path']).stat().st_size,
                             playable=is_playable_video_path(compression['path']))
        report = dict(media_roundtrip=roundtrip, model=settings['model_name'], confidence=settings['confidence'], image_size=settings['yolo_image_size'],
                      capabilities=pipeline.capabilities, samples=results)
    target = ROOT / 'docs' / 'debug-offline-vision.json'
    target.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()

