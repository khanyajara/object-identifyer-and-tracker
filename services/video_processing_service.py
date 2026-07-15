from datetime import datetime, timedelta
from pathlib import Path

import cv2

from core.video_io import DEFAULT_VIDEO_EXTENSION, finalize_video_file, open_video_writer
from core.vision_pipeline import VisionPipeline
from services.compression_service import CompressionService
from services.detection_log_service import DetectionLogService
from services.video_service import (
    COMPRESSED_PROCESSED_VIDEOS_DIR,
    PROCESSED_VIDEOS_DIR,
    THUMBNAILS_DIR,
    VideoService,
)


class VideoProcessingService:
    def __init__(self, settings, model=None, ocr_reader=None):
        self.settings = settings
        self.model = model
        self.ocr_reader = ocr_reader
        self.video_service = VideoService()

    def process(self, record):
        original_path = Path(
            record.get("original_video_path") or record["video_path"]
        )
        output_path = PROCESSED_VIDEOS_DIR / (
            f'recording_{record["video_id"]}_processed{DEFAULT_VIDEO_EXTENSION}'
        )
        temporary_path = output_path.with_suffix(".raw")
        record["processing_status"] = "Processing detection overlays..."
        record["processing_error"] = None
        self.video_service.save(record)
        try:
            events, frame_count, actual_temporary_path = self._process_video(
                record, original_path, temporary_path
            )
            output_path = output_path.with_suffix(actual_temporary_path.suffix)
            finalize_video_file(actual_temporary_path, output_path)
            compressed_target = (
                COMPRESSED_PROCESSED_VIDEOS_DIR
                / output_path.with_suffix(".mp4").name
            )
            compression = CompressionService().compress_for_playback(
                output_path, compressed_target
            )
            record["processed_compression_status"] = (
                "success" if compression["ok"] else "fallback"
            )
            record["processed_compression_error"] = (
                None if compression["ok"] else compression.get("error")
            )
            record["processed_compression_message"] = compression["message"]
            self._replace_detection_metadata(record, events)
            record["processed_video_path"] = str(output_path)
            if output_path.suffix.lower() == ".mp4":
                record["processed_mp4_path"] = str(output_path)
            record["processed_video_format"] = output_path.suffix.lstrip(".")
            record["compressed_processed_path"] = (
                str(compression["path"]) if compression["ok"] else None
            )
            record["compressed_mp4_path"] = (
                str(compression["path"]) if compression["ok"] else None
            )
            record["playback_video_path"] = (
                str(compression["path"]) if compression["ok"] else str(output_path)
            )
            record["playback_source"] = record["playback_video_path"]
            record["playback_format"] = "mp4" if compression["ok"] else output_path.suffix.lstrip(".")
            record["upload_video_path"] = (
                str(compression["path"]) if compression["ok"] else str(output_path)
            )
            record["upload_mp4_path"] = record["upload_video_path"]
            thumbnail = CompressionService().create_thumbnail(
                record["playback_video_path"],
                THUMBNAILS_DIR / f'{record["video_id"]}.jpg',
            )
            record["thumbnail_path"] = (
                str(thumbnail["path"]) if thumbnail["ok"] else None
            )
            record["thumbnail_status"] = "created" if thumbnail["ok"] else "failed"
            record["thumbnail_error"] = thumbnail.get("error")
            record["processed_frame_count"] = frame_count
            record.pop("processed_temporary_path", None)
            record["processing_status"] = "Processed video saved successfully."
            record["processing_error"] = None
        except Exception as exc:
            cleanup_paths = {temporary_path}
            if record.get("processed_temporary_path"):
                cleanup_paths.add(Path(record["processed_temporary_path"]))
            for path in cleanup_paths:
                try:
                    path.unlink()
                except OSError:
                    pass
            record["processed_video_path"] = None
            record["processing_status"] = "Post-processing failed"
            record["processing_error"] = str(exc)
        self.video_service.save(record)
        return record

    def _process_video(self, record, original_path, temporary_path):
        if not original_path.exists() or not original_path.stat().st_size:
            raise RuntimeError("Original video is unavailable for processing.")
        capture = cv2.VideoCapture(str(original_path))
        if not capture.isOpened():
            raise RuntimeError("Could not open the original saved video.")
        writer = None
        try:
            width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
            fps = float(capture.get(cv2.CAP_PROP_FPS) or record.get("fps") or 0)
            if width <= 0 or height <= 0:
                raise RuntimeError("Saved video has an invalid resolution.")
            if not 1 <= fps <= 120:
                fps = float(self.settings["target_camera_fps"])
            writer, actual_temporary_path, codec = open_video_writer(
                temporary_path, fps, (width, height)
            )
            record["processed_video_codec"] = codec
            record["processed_temporary_path"] = str(actual_temporary_path)
            pipeline = VisionPipeline(
                self.settings["model_name"],
                self.settings["confidence"],
                self.settings["yolo_image_size"],
                True,
                self.settings["enable_ocr"],
                self.settings["ocr_interval_seconds"],
                self.model,
                self.ocr_reader,
            )
            events = []
            frame_number = 0
            started_at = self._parse_started_at(record.get("started_at"))
            while True:
                ok, frame = capture.read()
                if not ok:
                    break
                frame_number += 1
                media_seconds = (frame_number - 1) / fps
                annotated, event = pipeline.process(
                    frame,
                    frame_number,
                    fps,
                    media_timestamp_seconds=media_seconds,
                )
                event["video_id"] = record["video_id"]
                event["timestamp"] = (
                    started_at + timedelta(seconds=media_seconds)
                ).isoformat()
                for item in event.get("objects", []):
                    item["video_id"] = record["video_id"]
                event["tracking_ids"] = sorted(
                    {
                        item["tracking_id"]
                        for item in event.get("objects", [])
                        if item.get("tracking_id") is not None
                    }
                )
                events.append(event)
                writer.write(annotated)
            if frame_number == 0:
                raise RuntimeError("The original video contains no frames.")
            return events, frame_number, actual_temporary_path
        finally:
            capture.release()
            if writer is not None:
                writer.release()

    def _replace_detection_metadata(self, record, events):
        log_service = DetectionLogService(record, save_snapshots=False)
        log_service.replace_all([(event, None) for event in events])

    @staticmethod
    def _parse_started_at(value):
        if value:
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                pass
        return datetime.now().astimezone()
