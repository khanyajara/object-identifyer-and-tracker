import copy
import atexit
import os
import queue
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import cv2

from core.annotation import annotate_frame
from core.video_io import (
    compress_video_for_playback,
    finalize_video_file,
    open_video_writer,
)
from core.vision_pipeline import VisionPipeline
from services.detection_log_service import DetectionLogService
from services.video_service import COMPRESSED_VIDEOS_DIR, VideoService


class CameraManager:
    def __init__(self, settings, model=None, ocr_reader=None):
        self.settings = settings
        self.pipeline = VisionPipeline(
            settings["model_name"],
            settings["confidence"],
            settings["yolo_image_size"],
            settings["enable_tracking"],
            settings["enable_ocr"],
            settings["ocr_interval_seconds"],
            model,
            ocr_reader,
        )
        self.video_service = VideoService()
        self.cap = self._open_camera()
        self.actual_width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.actual_height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        reported_fps = float(self.cap.get(cv2.CAP_PROP_FPS) or 0)
        self.recording_fps = (
            reported_fps if 1 <= reported_fps <= 120
            else float(settings["target_camera_fps"])
        )
        self.record = self.video_service.create_record(
            settings["camera_id"],
            self.recording_fps,
            f"{self.actual_width}x{self.actual_height}",
            settings.get("device_id", "roadwatch_local_01"),
        )
        self.record["camera_index"] = settings["camera_index"]
        self.record["performance"] = {
            "mode": settings["performance_mode"],
            "preview_mode": "opencv-streamlit",
            "ai_process_interval_seconds": settings[
                "ai_process_interval_seconds"
            ],
            "ai_resolution": (
                f'{settings["ai_frame_width"]}x'
                f'{settings["ai_frame_height"]}'
            ),
            "enable_ocr": settings["enable_ocr"],
            "enable_tracking": settings["enable_tracking"],
            "yolo_image_size": settings["yolo_image_size"],
        }
        self.log_service = DetectionLogService(
            self.record, settings["save_snapshots"]
        )
        requested_video_path = Path(self.record["video_path"])
        self.raw_path = requested_video_path.with_suffix(".raw")
        self.writer, self.raw_path, self.video_codec = self._open_writer()
        final_path = requested_video_path.with_suffix(self.raw_path.suffix)
        self.record["filename"] = final_path.name
        self.record["video_path"] = str(final_path)
        self.record["original_video_path"] = str(final_path)
        self.record["video_format"] = final_path.suffix.lstrip(".")
        self.record["video_codec"] = self.video_codec
        self.video_service.save(self.record)
        if final_path.suffix != requested_video_path.suffix:
            try:
                requested_video_path.with_suffix(".json").unlink()
            except OSError:
                pass
        self.active = True
        self.started = time.monotonic()
        self.frame_number = 0
        self.latest_error = None
        self._latest_frame = None
        self._latest_frame_number = 0
        self._latest_event = None
        self._detection_entries = []
        self._flushed_detection_count = 0
        self._state_lock = threading.Lock()
        self._detection_lock = threading.Lock()
        self._flush_lock = threading.Lock()
        self._stop = threading.Event()
        self._ai_queue = queue.Queue(
            maxsize=max(1, int(settings.get("ai_queue_maxsize", 1)))
        )
        self._log_queue = queue.Queue(maxsize=128)
        self._metrics = {
            "camera_fps": 0.0,
            "camera_reported_fps": reported_fps,
            "ai_fps": 0.0,
            "processing_time_ms": 0.0,
            "captured_frames": 0,
            "recorded_frames": 0,
            "ai_sampled_frames": 0,
            "ai_dropped_samples": 0,
            "current_performance_mode": settings["performance_mode"],
            "preview_mode": "opencv-streamlit",
            "preview_status": "OpenCV camera live",
            "active_resolution": (
                f"{self.actual_width} x {self.actual_height}"
            ),
            "camera_index": settings["camera_index"],
        }
        self._capture_thread = threading.Thread(
            target=self._capture_loop, daemon=True, name="camera-capture"
        )
        self._sample_thread = threading.Thread(
            target=self._sample_loop, daemon=True, name="vision-sampler"
        )
        self._ai_thread = threading.Thread(
            target=self._ai_loop, daemon=True, name="vision-ai"
        )
        self._log_thread = threading.Thread(
            target=self._log_loop, daemon=True, name="vision-log"
        )
        for thread in (
            self._capture_thread,
            self._sample_thread,
            self._ai_thread,
            self._log_thread,
        ):
            thread.start()
        atexit.register(self._stop_on_exit)

    @property
    def elapsed(self):
        return time.monotonic() - self.started

    @property
    def metrics(self):
        with self._state_lock:
            return self._metrics.copy()

    @property
    def capabilities(self):
        return self.pipeline.capabilities

    def _open_camera(self):
        index = int(self.settings["camera_index"])
        backend = cv2.CAP_DSHOW if os.name == "nt" else cv2.CAP_ANY
        cap = cv2.VideoCapture(index, backend)
        if not cap.isOpened() and backend != cv2.CAP_ANY:
            cap.release()
            cap = cv2.VideoCapture(index)
        if not cap.isOpened():
            raise RuntimeError(
                f"Could not open camera index {index}. "
                "Check that another app is not using the webcam."
            )
        cap.set(
            cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG")
        )
        cap.set(
            cv2.CAP_PROP_BUFFERSIZE,
            int(self.settings.get("camera_buffer_size", 1)),
        )
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.settings["camera_width"])
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.settings["camera_height"])
        cap.set(cv2.CAP_PROP_FPS, self.settings["target_camera_fps"])
        return cap

    def _open_writer(self):
        try:
            return open_video_writer(
                self.raw_path,
                self.recording_fps,
                (self.actual_width, self.actual_height),
            )
        except RuntimeError as exc:
            self.cap.release()
            raise RuntimeError("Could not create the recording file.") from exc

    def _capture_loop(self):
        fps_started, fps_frames = time.monotonic(), 0
        while not self._stop.is_set():
            ok, frame = self.cap.read()
            if not ok:
                with self._state_lock:
                    self.latest_error = "Camera frame read failed."
                time.sleep(0.02)
                continue
            self.writer.write(frame)
            now = time.monotonic()
            fps_frames += 1
            with self._state_lock:
                self.frame_number += 1
                self._latest_frame_number = self.frame_number
                self._latest_frame = frame
                self._metrics["captured_frames"] += 1
                self._metrics["recorded_frames"] += 1
                elapsed = now - fps_started
                if elapsed >= 1:
                    self._metrics["camera_fps"] = fps_frames / elapsed
                    fps_started, fps_frames = now, 0

    def _sample_loop(self):
        interval = float(self.settings["ai_process_interval_seconds"])
        last_sample_time = 0.0
        last_frame_number = 0
        while not self._stop.wait(0.01):
            now = time.monotonic()
            if now - last_sample_time < interval:
                continue
            with self._state_lock:
                number = self._latest_frame_number
                frame = (
                    self._latest_frame.copy()
                    if self._latest_frame is not None
                    else None
                )
            if frame is None or number == last_frame_number:
                continue
            last_sample_time = now
            last_frame_number = number
            ai_width = int(self.settings["ai_frame_width"])
            ai_height = int(self.settings["ai_frame_height"])
            ai_frame = cv2.resize(
                frame, (ai_width, ai_height), interpolation=cv2.INTER_AREA
            )
            item = (number, ai_frame, frame)
            with self._state_lock:
                self._metrics["ai_sampled_frames"] += 1
            self._offer_latest(item)

    def _offer_latest(self, item):
        try:
            self._ai_queue.put_nowait(item)
        except queue.Full:
            try:
                self._ai_queue.get_nowait()
                self._ai_queue.task_done()
            except queue.Empty:
                pass
            self._ai_queue.put_nowait(item)
            with self._state_lock:
                self._metrics["ai_dropped_samples"] += 1

    @staticmethod
    def _scale_box(box, scale_x, scale_y):
        box["x"] = round(float(box.get("x", 0)) * scale_x)
        box["y"] = round(float(box.get("y", 0)) * scale_y)
        box["width"] = round(float(box.get("width", 0)) * scale_x)
        box["height"] = round(float(box.get("height", 0)) * scale_y)

    def _scale_event(self, event, original_frame, ai_frame):
        original_height, original_width = original_frame.shape[:2]
        ai_height, ai_width = ai_frame.shape[:2]
        scale_x = original_width / max(ai_width, 1)
        scale_y = original_height / max(ai_height, 1)
        for item in event.get("objects", []):
            self._scale_box(item.setdefault("box", {}), scale_x, scale_y)
        for plate in event.get("plates", []):
            self._scale_box(plate.setdefault("box", {}), scale_x, scale_y)
        event["source_resolution"] = f"{original_width}x{original_height}"
        event["ai_resolution"] = f"{ai_width}x{ai_height}"
        return event

    def _ai_loop(self):
        started, count = time.monotonic(), 0
        while not self._stop.is_set() or not self._ai_queue.empty():
            try:
                number, ai_frame, original_frame = self._ai_queue.get(
                    timeout=0.2
                )
            except queue.Empty:
                continue
            began = time.monotonic()
            try:
                _, event = self.pipeline.process(
                    ai_frame, number, self.metrics["camera_fps"]
                )
                event = self._scale_event(event, original_frame, ai_frame)
                event["video_id"] = self.record["video_id"]
                event["timestamp"] = datetime.now(timezone.utc).isoformat()
                for item in event.get("objects", []):
                    item["video_id"] = self.record["video_id"]
                event["tracking_ids"] = sorted(
                    {
                        item["tracking_id"]
                        for item in event.get("objects", [])
                        if item.get("tracking_id") is not None
                    }
                )
                annotated = annotate_frame(
                    original_frame.copy(),
                    event["objects"],
                    event["plates"],
                    event["movement_detected"],
                    self.metrics["camera_fps"],
                )
                count += 1
                with self._state_lock:
                    self._latest_event = event
                    self.latest_error = None
                    self._metrics["processing_time_ms"] = (
                        time.monotonic() - began
                    ) * 1000
                    self._metrics["ai_fps"] = count / max(
                        time.monotonic() - started, 0.001
                    )
                with self._detection_lock:
                    self._detection_entries.append((event, annotated))
                try:
                    self._log_queue.put_nowait(True)
                except queue.Full:
                    pass
            except Exception as exc:
                with self._state_lock:
                    self.latest_error = f"AI frame {number}: {exc}"
            finally:
                self._ai_queue.task_done()

    def _log_loop(self):
        last_flush = time.monotonic()
        interval = float(self.settings["log_flush_interval_seconds"])
        while (
            not self._stop.is_set()
            or self._ai_thread.is_alive()
            or not self._log_queue.empty()
        ):
            try:
                self._log_queue.get(timeout=0.2)
                self._log_queue.task_done()
            except queue.Empty:
                pass
            should_flush = (
                time.monotonic() - last_flush >= interval
                or (self._stop.is_set() and not self._ai_thread.is_alive())
            )
            if should_flush:
                self._flush_detections()
                last_flush = time.monotonic()

    def _flush_detections(self):
        with self._flush_lock:
            with self._detection_lock:
                pending = self._detection_entries[
                    self._flushed_detection_count:
                ]
                if not pending:
                    return
                pending = list(pending)
            self.log_service.add_many(pending)
            self.video_service.save(self.record)
            with self._detection_lock:
                self._flushed_detection_count += len(pending)

    def get_dashboard_state(self):
        with self._state_lock:
            frame = (
                self._latest_frame.copy()
                if self._latest_frame is not None
                else None
            )
            event = copy.deepcopy(self._latest_event)
            metrics = self._metrics.copy()
            error = self.latest_error
        if frame is not None and event:
            frame = annotate_frame(
                frame,
                event.get("objects", []),
                event.get("plates", []),
                event.get("movement_detected", False),
                metrics["camera_fps"],
            )
        return frame, event, metrics, error

    def stop(self):
        if not self.active:
            return self.record
        self.active = False
        self._stop.set()
        self._capture_thread.join(timeout=2)
        self.cap.release()
        self._capture_thread.join(timeout=2)
        self._sample_thread.join(timeout=2)
        self._ai_thread.join(timeout=15)
        self._log_thread.join(timeout=8)
        self._flush_detections()
        self.writer.release()
        self.record["ended_at"] = datetime.now(timezone.utc).isoformat()
        self.record["duration_seconds"] = round(self.elapsed, 1)
        self.record["performance_metrics"] = self.metrics
        self.record["frame_accounting"] = {
            "captured_frames": self.metrics["captured_frames"],
            "recorded_frames": self.metrics["recorded_frames"],
            "ai_sampled_frames": self.metrics["ai_sampled_frames"],
            "ai_dropped_samples": self.metrics["ai_dropped_samples"],
        }
        final_path = Path(self.record["video_path"])
        if self.raw_path.exists() and self.raw_path.stat().st_size:
            finalize_video_file(self.raw_path, final_path)
            compressed_target = COMPRESSED_VIDEOS_DIR / final_path.with_suffix(".mp4").name
            compression = compress_video_for_playback(final_path, compressed_target)
            self.record["compression_status"] = "success" if compression["ok"] else "fallback"
            self.record["compression_error"] = None if compression["ok"] else compression.get("error")
            self.record["compression_message"] = compression["message"]
            if compression["ok"]:
                self.record["compressed_original_path"] = str(compression["path"])
                self.record["compressed_original_mp4_path"] = str(compression["path"])
                self.record["playback_video_path"] = str(compression["path"])
                self.record["playback_source"] = str(compression["path"])
                self.record["playback_format"] = "mp4"
            else:
                self.record["compressed_original_path"] = None
                self.record["playback_video_path"] = str(final_path)
                self.record["playback_source"] = str(final_path)
                self.record["playback_format"] = final_path.suffix.lstrip(".")
            self.record["video_path"] = str(final_path)
            self.record["original_video_path"] = str(final_path)
            if final_path.suffix.lower() == ".mp4":
                self.record["original_mp4_path"] = str(final_path)
            self.record["filename"] = final_path.name
            self.record["video_format"] = final_path.suffix.lstrip(".")
            self.record["recording_status"] = "Complete"
        else:
            self.record["recording_status"] = "No camera frames received"
        self.video_service.save(self.record)
        try:
            atexit.unregister(self._stop_on_exit)
        except Exception:
            pass
        return self.record

    def _stop_on_exit(self):
        if self.active:
            self.stop()


# Keep the old import name working for integrations that already use it.
OpenCVVisionRecorder = CameraManager
