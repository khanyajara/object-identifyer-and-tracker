"""Session-owned AI initialized on the inference worker, never camera startup."""
import logging
import threading
import time


class LazyVisionPipeline:
    def __init__(self, settings):
        self.settings = dict(settings)
        self._pipeline = None
        self._lock = threading.Lock()
        self._retry_at = 0
        self._error = None

    def process(self, frame, frame_number, camera_fps, **kwargs):
        with self._lock:
            if self._pipeline is None:
                if time.monotonic() < self._retry_at:
                    return frame, {"objects": [], "error": self._error}
                try:
                    from core.vision_pipeline import VisionPipeline
                    s = self.settings
                    self._pipeline = VisionPipeline(s["model_name"], s["confidence"], s["yolo_image_size"],
                                                    s["enable_tracking"], s["enable_ocr"], s["ocr_interval_seconds"])
                except Exception as exc:
                    self._error = str(exc)
                    self._retry_at = time.monotonic() + 30
                    logging.getLogger("roadwatch.camera").exception("[Camera] AI initialization failed; capture continues")
                    return frame, {"objects": [], "error": self._error}
            return self._pipeline.process(frame, frame_number, camera_fps, **kwargs)
