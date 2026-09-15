"""Session-owned WebRTC input adapted to the existing recording/AI channel."""
import threading
import time
import cv2
from core.dual_camera import CameraChannel, STATUS_FAILED


class BrowserFrameSource:
    def __init__(self, width, height, fps):
        self.width, self.height, self.fps = width, height, fps
        self._condition = threading.Condition()
        self._pending = None
        self.last_received = -float("inf")
        self.closed = False
        self.dropped = 0

    @property
    def ready(self):
        with self._condition:
            return not self.closed and time.monotonic() - self.last_received < 2

    def offer(self, frame):
        if frame is None or frame.ndim != 3 or frame.shape[2] != 3:
            return
        frame = cv2.resize(frame, (self.width, self.height))
        with self._condition:
            if self.closed:
                return
            if self._pending is not None:
                self.dropped += 1
            self._pending = frame
            self.last_received = time.monotonic()
            self._condition.notify()

    def read(self):
        with self._condition:
            self._condition.wait_for(lambda: self._pending is not None or self.closed, timeout=.25)
            if self.closed or time.monotonic() - self.last_received >= 2:
                self._pending = None
                return False, None
            frame, self._pending = self._pending, None
            return frame is not None, frame

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
        with self._browser_lock:
            self.browser_source.release()
            self.browser_source = BrowserFrameSource(self.config.width, self.config.height, self.config.target_fps)
            return self.browser_source

    def _open_capture(self):
        return self.browser_source if self.browser_source.ready else None

    def _open_locked(self):
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
