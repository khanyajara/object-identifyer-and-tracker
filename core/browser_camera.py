"""Session-owned WebRTC input adapted to the existing recording/AI channel."""
import threading
import time
import logging
from collections import deque
import cv2
import numpy as np
from core.dual_camera import CameraChannel, STATUS_FAILED

LOGGER = logging.getLogger("roadwatch.camera")


class BrowserFrameSource:
    def __init__(self, width, height, fps):
        self.width, self.height, self.fps = width, height, fps
        self._condition = threading.Condition()
        self._pending = None
        self.last_received = -float("inf")
        self.closed = False
        self.dropped = 0
        self.frames_received = 0
        self.frames_processed = 0
        self.started_at = time.monotonic()
        self.last_error = None
        self._arrival_times = deque(maxlen=60)

    @property
    def ready(self):
        with self._condition:
            return not self.closed and time.monotonic() - self.last_received < 2

    def offer(self, frame, color_format="bgr"):
        """Normalize once to owned uint8 H x W x 3 BGR, bounded to one frame."""
        if not isinstance(frame, np.ndarray) or frame.dtype != np.uint8 or frame.ndim != 3 or frame.shape[2] != 3 or not frame.size:
            raise ValueError("Camera frame must be a nonempty uint8 H x W x 3 array")
        if color_format not in {"bgr", "rgb"}:
            raise ValueError("Camera color format must be bgr or rgb")
        if color_format == "rgb":
            frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        frame = cv2.resize(frame, (self.width, self.height)) if frame.shape[:2] != (self.height, self.width) else frame.copy()
        with self._condition:
            if self.closed:
                return
            if self._pending is not None:
                self.dropped += 1
            self._pending = frame
            self.last_error = None
            self.frames_received += 1
            if self.frames_received == 1:
                LOGGER.info("[Camera] first browser frame received: %sx%s", self.width, self.height)
            self.last_received = time.monotonic()
            self._arrival_times.append(self.last_received)
            self._condition.notify()

    def read(self):
        with self._condition:
            self._condition.wait_for(lambda: self._pending is not None or self.closed, timeout=.25)
            if self.closed or time.monotonic() - self.last_received >= 2:
                self._pending = None
                return False, None
            frame, self._pending = self._pending, None
            self.frames_processed += int(frame is not None)
            return frame is not None, frame

    def health(self):
        with self._condition:
            age = time.monotonic() - self.last_received
            status = "camera_active" if age < 2 and not self.closed else "camera_stale"
            if not self.frames_received:
                status = "camera_permission_required" if time.monotonic() - self.started_at < 20 else "camera_error"
            elif age >= 5 or self.closed:
                status = "camera_reconnecting"
            elapsed = self._arrival_times[-1] - self._arrival_times[0] if len(self._arrival_times) > 1 else 0
            return dict(backend="browser", status=status, last_frame_at=self.last_received if self.frames_received else None,
                        estimated_fps=(len(self._arrival_times)-1)/elapsed if elapsed > 0 and age < 2 else 0,
                        width=self.width, height=self.height, frames_received=self.frames_received,
                        frames_processed=self.frames_processed, frames_dropped=self.dropped, last_error=self.last_error)

    def isOpened(self):
        return self.ready

    def get(self, key):
        return {cv2.CAP_PROP_FRAME_WIDTH: self.width, cv2.CAP_PROP_FRAME_HEIGHT: self.height,
                cv2.CAP_PROP_FPS: self.fps}.get(key, 0)

    def release(self):
        with self._condition:
            self.closed = True
            self._pending = None
            self._condition.notify_all()


class BrowserCameraChannel(CameraChannel):
    def __init__(self, config):
        super().__init__(config)
        self.browser_source = BrowserFrameSource(config.width, config.height, config.target_fps)
        self._browser_lock = threading.Lock()

    def new_connection(self):
        # The returned source belongs to this particular peer connection. A late
        # callback from an old connection cannot close or feed the replacement.
        with self._browser_lock, self._lifecycle_lock:
            self._stop_event.set()
            self.browser_source.release()
            if self._thread is not None:
                self._thread.join(timeout=2)
                if self._thread.is_alive():
                    raise RuntimeError("Previous browser capture worker is still stopping")
            self._capture = None
            self.browser_source = BrowserFrameSource(self.config.width, self.config.height, self.config.target_fps)
            LOGGER.info("[Camera] browser stream starting")
            return self.browser_source

    def ensure_capture(self):
        with self._lifecycle_lock:
            if not self.is_live:
                # Base recovery releases its handle. This source is fed by the
                # current peer and must remain open when fresh frames resume.
                self._capture = None
            return super().ensure_capture()

    def _open_capture(self):
        return self.browser_source if self.browser_source.ready else None

    def _open_locked(self):
        if not self.config.enabled:
            return False
        if not self.browser_source.ready:
            self.error = "Waiting for browser camera frames. Allow camera access; check the camera permission and default device in your browser settings."
            self.status = STATUS_FAILED
            return False
        return super()._open_locked()

    def _measure_fps(self, cap, sample_frames=20):
        return float(self.config.target_fps)

    def _try_reopen(self):
        # Never fall back to VideoCapture or another visitor's camera.
        return False

    def _capture_loop(self):
        try:
            super()._capture_loop()
        finally:
            # A disconnected tab must not leave an open writer behind.
            # Do not take the lifecycle lock: a UI stop may be joining us while
            # holding it. The writer lock is shared with normal recorder stop.
            with self._write_lock:
                if self._recording:
                    self._record_stopped_at = time.time()
                self._recording = False
                if self._writer is not None:
                    self._writer.release()
                    self._writer = None

    def get_preview(self):
        return super().get_preview() if self.browser_source.ready else None

    def release(self, force=False):
        self.browser_source.release()
        super().release(force=True)
