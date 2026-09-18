"""
core/dual_camera.py

Dual camera capture for Roadwatch Vision Recorder.

Design rules carried over from the single-camera recorder:

    Record every camera frame.
    Process AI without blocking recording.
    Save detections under the video they belong to.

Each camera is an independent channel with its own OpenCV capture, its own
background thread, and its own MP4 writer. One camera failing (unplugged,
busy, USB bandwidth starved) never stops the other one. AI never touches the
capture loop: the loop writes the frame, stores the latest frame for preview,
and drops a sampled copy into a size-1 queue that AI consumers may or may not
read in time.

Typical use:

    from core.dual_camera import DualCameraManager, CameraConfig, FRONT, REAR

    manager = DualCameraManager.from_settings(settings)
    manager.open()
    session = manager.start_recording()
    ...
    session = manager.stop_recording()
    manager.release()
"""

from __future__ import annotations

import logging
import os
import platform
import queue
import threading
import time
import uuid
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Sequence, Tuple

from core.vision_pipeline import VisionPipeline
from core.camera_sources import windows_camera_devices, droidcam_index
from core.frame_source import CameraSource, LocalOpenCVCameraSource

import cv2
import numpy as np

from core.time_utils import utc_now

LOGGER = logging.getLogger("roadwatch.dual_camera")

FRONT = "front"
REAR = "rear"

# Browser friendly first, legacy fallback second.
_WEBM_CODECS = ("VP80", "VP90")
_MP4_CODECS = ("mp4v", "avc1")  # intermediate; playback export uses H.264

# Status values a channel can report.
STATUS_IDLE = "idle"
STATUS_LIVE = "live"
STATUS_RECORDING = "recording"
STATUS_DEGRADED = "degraded"
STATUS_FAILED = "failed"
STATUS_DISABLED = "disabled"


def _default_backend() -> int:
    system = platform.system().lower()
    if system == "windows":
        return cv2.CAP_DSHOW
    if system == "linux":
        return cv2.CAP_V4L2
    if system == "darwin":
        return cv2.CAP_AVFOUNDATION
    return cv2.CAP_ANY


def _new_short_id() -> str:
    return uuid.uuid4().hex[:6]


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


@dataclass
class CameraConfig:
    """One physical camera in the rig."""

    index: int
    role: str = FRONT
    label: str = ""
    enabled: bool = True
    width: int = 1280
    height: int = 720
    target_fps: int = 30
    buffer_size: int = 1
    # MJPG lets two USB cameras share one controller. Without it most webcams
    # fall back to raw YUY2 and a single 720p30 stream saturates USB 2.0.
    capture_fourcc: str = "MJPG"
    backend: Optional[int] = None
    rotate: int = 0  # 0, 90, 180, 270 - rear cameras are often mounted upside down
    mirror: bool = False
    ai_enabled: bool = True
    ai_sample_interval: float = 0.5

    def __post_init__(self) -> None:
        if self.backend is None:
            self.backend = _default_backend()
        if not self.label:
            self.label = self.role.replace("_", " ").title()
        if self.rotate not in (0, 90, 180, 270):
            raise ValueError("rotate must be one of 0, 90, 180, 270")


def camera_configs_from_settings(settings: Optional[dict] = None) -> List[CameraConfig]:
    """Build front/rear configs from a settings dict or environment variables.

    Recognised keys (settings dict first, then env, then default):

        DUAL_CAMERA_ENABLED, FRONT_CAMERA_INDEX, REAR_CAMERA_INDEX,
        CAMERA_WIDTH, CAMERA_HEIGHT, TARGET_CAMERA_FPS, CAMERA_BUFFER_SIZE,
        REAR_CAMERA_WIDTH, REAR_CAMERA_HEIGHT, REAR_CAMERA_ROTATE,
        REAR_CAMERA_AI_ENABLED, AI_PROCESS_INTERVAL_SECONDS
    """

    settings = settings or {}

    def get(key: str, default):
        if key in settings and settings[key] not in (None, ""):
            return settings[key]
        lower_key = key.lower()
        if lower_key in settings and settings[lower_key] not in (None, ""):
            return settings[lower_key]
        return os.getenv(key, default)

    def as_int(key: str, default: int) -> int:
        try:
            return int(get(key, default))
        except (TypeError, ValueError):
            return default

    def as_float(key: str, default: float) -> float:
        try:
            return float(get(key, default))
        except (TypeError, ValueError):
            return default

    def as_bool(key: str, default: bool) -> bool:
        value = get(key, default)
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "on")

    def has_setting(key: str) -> bool:
        return (
            (key in settings and settings[key] not in (None, ""))
            or os.getenv(key) not in (None, "")
        )

    width = as_int("CAMERA_WIDTH", 1280)
    height = as_int("CAMERA_HEIGHT", 720)
    fps = as_int("TARGET_CAMERA_FPS", 30)
    buffer_size = as_int("CAMERA_BUFFER_SIZE", 1)
    ai_interval = as_float("AI_PROCESS_INTERVAL_SECONDS", 0.5)
    dual_enabled = as_bool("DUAL_CAMERA_ENABLED", True)
    front_index = as_int("FRONT_CAMERA_INDEX", as_int("CAMERA_INDEX", 0))
    rear_index = as_int("REAR_CAMERA_INDEX", 1)

    front = CameraConfig(
        index=front_index,
        role=FRONT,
        label=str(get("FRONT_CAMERA_LABEL", "Front Road")),
        enabled=True,
        width=width,
        height=height,
        target_fps=fps,
        buffer_size=buffer_size,
        rotate=as_int("FRONT_CAMERA_ROTATE", 0),
        ai_enabled=True,
        # Two cameras share one CPU. Give each channel its own slot so the
        # front camera cannot starve the rear one.
        ai_sample_interval=ai_interval,
    )

    if has_setting("REAR_CAMERA_MIRROR"):
        rear_mirror = as_bool("REAR_CAMERA_MIRROR", False)
    else:
        rear_mirror = rear_index != front_index

    rear = CameraConfig(
        index=rear_index,
        role=REAR,
        label=str(get("REAR_CAMERA_LABEL", "Rear / Cabin")),
        enabled=dual_enabled,
        width=as_int("REAR_CAMERA_WIDTH", width),
        height=as_int("REAR_CAMERA_HEIGHT", height),
        target_fps=as_int("REAR_CAMERA_FPS", fps),
        buffer_size=buffer_size,
        rotate=as_int("REAR_CAMERA_ROTATE", 0),
        mirror=rear_mirror,
        ai_enabled=as_bool("REAR_CAMERA_AI_ENABLED", True),
        ai_sample_interval=ai_interval * 2,
    )

    computer_rear = str(get("REAR_CAMERA_SOURCE", "")).lower() == "computer"
    if computer_rear:
        # An explicit computer-camera assignment must survive an inconclusive
        # DirectShow format listing. CameraChannel.open verifies actual frames.
        devices = windows_camera_devices() or []
        computer = next((index for index, name, _ in devices
                         if not any(word in name.lower() for word in ("droidcam", "virtual", "obs"))), None)
        if computer is not None:
            rear.index = computer
        elif devices:
            rear.enabled = False
        rear.label = "Computer camera · Rear / Cabin"
        rear.ai_enabled = True
        fallback = droidcam_index()
        if fallback is not None and (not rear.enabled or fallback != rear.index):
            front.index = fallback
            front.label = "DroidCam"
        if rear.enabled and front.index == rear.index:
            front.enabled = False
    if not computer_rear and as_bool("DROIDCAM_FALLBACK_ENABLED", True):
        devices = windows_camera_devices()
        fallback = droidcam_index() if devices is not None else None
        if fallback is not None and (sum(usable for _, _, usable in devices) < 2 or front.index == rear.index):
            front.index = fallback
            front.label = "DroidCam"
            rear.enabled = False
    if front.enabled and rear.enabled and front.index == rear.index:
        rear.enabled = False  # one owner per physical OpenCV device
    return [front, rear]


# --------------------------------------------------------------------------
# Frame helpers
# --------------------------------------------------------------------------


def apply_orientation(frame: np.ndarray, rotate: int = 0, mirror: bool = False) -> np.ndarray:
    if rotate == 90:
        frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
    elif rotate == 180:
        frame = cv2.rotate(frame, cv2.ROTATE_180)
    elif rotate == 270:
        frame = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
    if mirror:
        frame = cv2.flip(frame, 1)
    return frame


def placeholder_frame(width: int, height: int, text: str) -> np.ndarray:
    """A dark tile used wherever a camera has no signal."""
    frame = np.full((height, width, 3), 24, dtype=np.uint8)
    cv2.rectangle(frame, (8, 8), (width - 8, height - 8), (60, 60, 60), 1)
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = max(0.5, min(width, height) / 700.0)
    size, _ = cv2.getTextSize(text, font, scale, 2)
    origin = ((width - size[0]) // 2, (height + size[1]) // 2)
    cv2.putText(frame, text, origin, font, scale, (170, 170, 170), 2, cv2.LINE_AA)
    return frame


def _stamp_label(frame: np.ndarray, text: str) -> np.ndarray:
    out = frame.copy()
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = max(0.45, out.shape[1] / 1400.0)
    size, _ = cv2.getTextSize(text, font, scale, 2)
    cv2.rectangle(out, (0, 0), (size[0] + 20, size[1] + 18), (0, 0, 0), -1)
    cv2.putText(out, text, (10, size[1] + 8), font, scale, (255, 255, 255), 2, cv2.LINE_AA)
    return out


def _resize_to_height(frame: np.ndarray, height: int) -> np.ndarray:
    if frame.shape[0] == height:
        return frame
    width = max(1, int(round(frame.shape[1] * height / frame.shape[0])))
    return cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)


def compose_side_by_side(
    left: Optional[np.ndarray],
    right: Optional[np.ndarray],
    height: int = 540,
    left_label: str = "",
    right_label: str = "",
    gap: int = 6,
) -> np.ndarray:
    """Two feeds in one frame. Missing feeds become placeholders, not crashes."""
    left = left if left is not None else placeholder_frame(960, 540, left_label or "No signal")
    right = right if right is not None else placeholder_frame(960, 540, right_label or "No signal")

    left = _resize_to_height(left, height)
    right = _resize_to_height(right, height)
    if left_label:
        left = _stamp_label(left, left_label)
    if right_label:
        right = _stamp_label(right, right_label)

    divider = np.full((height, gap, 3), 12, dtype=np.uint8)
    return np.hstack([left, divider, right])


def compose_pip(
    main: Optional[np.ndarray],
    inset: Optional[np.ndarray],
    scale: float = 0.28,
    margin: int = 18,
    corner: str = "br",
    main_label: str = "",
    inset_label: str = "",
) -> np.ndarray:
    """Main feed full frame, second feed as a corner inset."""
    if main is None:
        main = placeholder_frame(1280, 720, main_label or "No signal")
    out = main.copy()
    if main_label:
        out = _stamp_label(out, main_label)
    if inset is None:
        return out

    h, w = out.shape[:2]
    inset_h = max(80, int(h * scale))
    small = _resize_to_height(inset, inset_h)
    if inset_label:
        small = _stamp_label(small, inset_label)
    ih, iw = small.shape[:2]
    if iw > w - 2 * margin:
        small = cv2.resize(small, (w - 2 * margin, ih))
        ih, iw = small.shape[:2]

    y = margin if corner.startswith("t") else h - ih - margin
    x = margin if corner.endswith("l") else w - iw - margin
    cv2.rectangle(out, (x - 2, y - 2), (x + iw + 2, y + ih + 2), (255, 255, 255), 2)
    out[y : y + ih, x : x + iw] = small
    return out


def open_video_writer(
    path: str, fps: float, size: Tuple[int, int]
) -> Tuple[Optional[cv2.VideoWriter], Optional[str]]:
    """Open a writer, trying browser friendly codecs first.

    Returns (writer, codec_tag). Writer is None when every codec failed.
    """
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    codecs = _WEBM_CODECS if path.lower().endswith(".webm") else _MP4_CODECS
    fps = float(max(1.0, fps))
    for tag in codecs:
        writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*tag), fps, size)
        if writer.isOpened():
            LOGGER.info("Writer open: %s (%s, %.2f fps, %sx%s)", path, tag, fps, *size)
            return writer, tag
        writer.release()
        LOGGER.warning("Codec %s unavailable for %s", tag, path)
    return None, None


# --------------------------------------------------------------------------
# One camera
# --------------------------------------------------------------------------


class CameraChannel:
    """A single camera: capture thread, writer, latest frame, AI sample queue."""

    def __init__(self, config: CameraConfig):
        self.config = config
        self.role = config.role

        self._capture: Optional[CameraSource] = None
        self._writer: Optional[cv2.VideoWriter] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._frame_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._lifecycle_lock = threading.RLock()
        self.keep_capture_alive = False
        self.preview_ai_enabled = False

        self._latest_frame: Optional[np.ndarray] = None
        self._latest_index: int = -1
        self._latest_ts: float = 0.0

        self._ai_queue: "queue.Queue[Tuple[str, np.ndarray, int, float]]" = queue.Queue(maxsize=1)
        self._last_ai_push: float = 0.0

        self.status: str = STATUS_DISABLED if not config.enabled else STATUS_IDLE
        self.error: Optional[str] = None
        self.codec: Optional[str] = None

        self.video_id: Optional[str] = None
        self.video_path: Optional[str] = None
        self.filename: Optional[str] = None

        self.frames_read: int = 0
        self.frames_written: int = 0
        self.frames_dropped_for_ai: int = 0
        self.read_failures: int = 0
        self.reopen_count: int = 0

        self.actual_width: int = config.width
        self.actual_height: int = config.height
        self.reported_fps: float = float(config.target_fps)
        self.measured_fps: float = 0.0

        self._recording = False
        self._record_started_at: float = 0.0
        self._record_stopped_at: float = 0.0
        self._fps_window: List[float] = []

    # -- lifecycle ---------------------------------------------------------

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def is_live(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def open(self) -> bool:
        with self._lifecycle_lock:
            return self._open_locked()

    def _open_locked(self) -> bool:
        if self._stop_event.is_set() and self.is_live:
            return False  # a native read is still shutting down; never overlap it
        if not self.config.enabled:
            self.status = STATUS_DISABLED
            return False
        if self._capture is not None and self._capture.isOpened():
            return True

        cap = self._open_capture()
        if cap is None:
            self.status = STATUS_FAILED
            self.error = (
                f"Camera index {self.config.index} did not open. "
                "Check the index, close other apps using the camera, and confirm both "
                "cameras are on separate USB controllers."
            )
            LOGGER.error("[%s] %s", self.role, self.error)
            return False

        self._capture = cap
        self.actual_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or self.config.width
        self.actual_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or self.config.height
        self.reported_fps = float(cap.get(cv2.CAP_PROP_FPS) or self.config.target_fps)
        if self.reported_fps <= 1 or self.reported_fps > 240:
            self.reported_fps = float(self.config.target_fps)

        self.measured_fps = self._measure_fps(cap)
        self.error = None
        self.status = STATUS_LIVE
        LOGGER.info(
            "[%s] open index=%s %sx%s reported=%.1ffps measured=%.1ffps",
            self.role,
            self.config.index,
            self.actual_width,
            self.actual_height,
            self.reported_fps,
            self.measured_fps,
        )
        return True

    def _open_capture(self) -> Optional[CameraSource]:
        for backend in (self.config.backend, cv2.CAP_ANY):
            cap = cv2.VideoCapture(self.config.index, backend)
            if not cap.isOpened():
                cap.release()
                continue
            try:
                if self.config.capture_fourcc:
                    cap.set(
                        cv2.CAP_PROP_FOURCC,
                        cv2.VideoWriter_fourcc(*self.config.capture_fourcc),
                    )
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.width)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.height)
                cap.set(cv2.CAP_PROP_FPS, self.config.target_fps)
                cap.set(cv2.CAP_PROP_BUFFERSIZE, self.config.buffer_size)
            except Exception as exc:  # pragma: no cover - driver dependent
                LOGGER.debug("[%s] property set failed: %s", self.role, exc)

            for _ in range(5):  # warm up, first frames are often black
                cap.read()
            ok, frame = cap.read()
            if ok and frame is not None:
                return LocalOpenCVCameraSource(cap)
            cap.release()
        return None

    def _measure_fps(self, cap: CameraSource, sample_frames: int = 20) -> float:
        """Real throughput matters more than the driver's claim.

        Two cameras on one controller usually deliver less than the nominal
        rate, and writing at the nominal rate produces videos that play too
        fast. The measured value seeds the writer; the true value is
        recalculated on stop.
        """
        start = time.time()
        captured = 0
        for _ in range(sample_frames):
            ok, _ = cap.read()
            if ok:
                captured += 1
        elapsed = time.time() - start
        if captured < 2 or elapsed <= 0:
            return float(self.config.target_fps)
        return round(min(captured / elapsed, float(self.config.target_fps)), 2)

    def start(self, video_path: Optional[str] = None, video_id: Optional[str] = None) -> bool:
        """Start the capture thread. Passing video_path also starts recording."""
        with self._lifecycle_lock:
            return self._start_locked(video_path, video_id)

    def _start_locked(self, video_path=None, video_id=None):
        if self.is_live and (not video_path or self._recording):
            return True
        if not self.config.enabled:
            return False
        if not self.open():
            return False

        if video_path:
            fps = self.measured_fps or self.config.target_fps
            frame_size = self._output_size()
            writer, codec = open_video_writer(video_path, fps, frame_size)
            if writer is None:
                self.status = STATUS_DEGRADED
                self.error = "No usable MP4 video codec. Camera preview remains available."
                LOGGER.error("[%s] %s", self.role, self.error)
            else:
                with self._write_lock:
                    self._writer = writer
                    self.codec = codec
                    self.video_path = video_path
                    self.filename = os.path.basename(video_path)
                    self.video_id = video_id
                    self.frames_read = self.frames_written = 0
                    self.frames_dropped_for_ai = self.read_failures = 0
                    self._fps_window = []
                    self._record_started_at = time.time()
                    self._record_stopped_at = 0.0
                    self._recording = True
                    self.status = STATUS_RECORDING

        if self.is_live:
            return True  # writer attached; the existing capture thread stays alive
        self.frames_read = 0
        self.frames_written = 0
        self.frames_dropped_for_ai = 0
        self.read_failures = 0
        self._fps_window = []
        self._record_started_at = time.time()
        self._record_stopped_at = 0.0
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._capture_loop, name=f"camera-{self.role}", daemon=True
        )
        self._thread.start()
        return True

    def _output_size(self) -> Tuple[int, int]:
        if self.config.rotate in (90, 270):
            return (self.actual_height, self.actual_width)
        return (self.actual_width, self.actual_height)

    def ensure_capture(self) -> bool:
        """Background recovery that shares the recorder's lifecycle lock and writer."""
        with self._lifecycle_lock:
            if self.is_live:
                return not self._stop_event.is_set()
            if self._capture is not None:
                self._capture.release()
                self._capture = None
            if not self.open():
                return False
            self._stop_event.clear()
            self.status = STATUS_RECORDING if self._recording else STATUS_LIVE
            self._thread = threading.Thread(target=self._capture_loop, name=f"camera-{self.role}", daemon=True)
            self._thread.start()
            return True

    # -- capture loop ------------------------------------------------------

    def _capture_loop(self) -> None:
        consecutive_failures = 0
        last_tick = time.time()

        while not self._stop_event.is_set():
            capture = self._capture
            if capture is None:
                break

            ok, frame = capture.read()
            now = time.time()

            if not ok or frame is None:
                consecutive_failures += 1
                self.read_failures += 1
                if consecutive_failures >= 30:
                    if not self._try_reopen():
                        self.status = STATUS_FAILED
                        self.error = "Camera stopped delivering frames."
                        LOGGER.error("[%s] camera lost, giving up", self.role)
                        break
                    consecutive_failures = 0
                time.sleep(0.01)
                continue

            consecutive_failures = 0
            if self.config.rotate or self.config.mirror:
                frame = apply_orientation(frame, self.config.rotate, self.config.mirror)

            self.frames_read += 1

            # 1. Write first. Nothing below this may delay the next read.
            if self._recording and self._writer is not None:
                try:
                    with self._write_lock:
                        if not self._recording or self._writer is None:
                            continue
                        expected = self._output_size()
                        if (frame.shape[1], frame.shape[0]) != expected:
                            frame_to_write = cv2.resize(frame, expected)
                        else:
                            frame_to_write = frame
                        self._writer.write(frame_to_write)
                        self.frames_written += 1
                except Exception as exc:  # pragma: no cover
                    LOGGER.exception("[%s] write failed: %s", self.role, exc)
                    self.status = STATUS_DEGRADED
                    self.error = f"Write failed: {exc}"

            # 2. Publish latest frame for the UI.
            with self._frame_lock:
                self._latest_frame = frame
                self._latest_index = self.frames_read
                self._latest_ts = now

            # 3. Offer a sampled copy to AI. Never block, never queue up.
            if self.config.ai_enabled and (not self.keep_capture_alive or self._recording or self.preview_ai_enabled) and (now - self._last_ai_push) >= self.config.ai_sample_interval:
                self._last_ai_push = now
                item = (self.role, frame.copy(), self.frames_read, now)
                try:
                    self._ai_queue.put_nowait(item)
                except queue.Full:
                    try:
                        self._ai_queue.get_nowait()
                        self._ai_queue.put_nowait(item)
                        self.frames_dropped_for_ai += 1
                    except queue.Empty:  # pragma: no cover - race
                        pass

            # Rolling fps for the stats panel.
            delta = now - last_tick
            last_tick = now
            if 0 < delta < 2:
                self._fps_window.append(delta)
                if len(self._fps_window) > 60:
                    self._fps_window.pop(0)

        LOGGER.info("[%s] capture loop ended (read=%s written=%s)", self.role, self.frames_read, self.frames_written)

    def _try_reopen(self) -> bool:
        LOGGER.warning("[%s] reopening camera index %s", self.role, self.config.index)
        self.reopen_count += 1
        self.status = STATUS_DEGRADED
        try:
            if self._capture is not None:
                self._capture.release()
        except Exception:  # pragma: no cover
            pass
        self._capture = None
        time.sleep(1.0)
        cap = self._open_capture()
        if cap is None:
            return False
        self._capture = cap
        self.status = STATUS_RECORDING if self._recording else STATUS_LIVE
        self._last_ai_push = 0.0
        return True

    # -- consumers ---------------------------------------------------------

    def get_preview(self) -> Optional[np.ndarray]:
        with self._frame_lock:
            if self._latest_frame is None:
                return None
            return self._latest_frame.copy()

    def get_latest(self) -> Tuple[Optional[np.ndarray], int, float]:
        with self._frame_lock:
            if self._latest_frame is None:
                return None, -1, 0.0
            return self._latest_frame.copy(), self._latest_index, self._latest_ts

    def get_ai_frame(self) -> Optional[Tuple[str, np.ndarray, int, float]]:
        """Non-blocking. Returns (role, frame, frame_number, epoch) or None."""
        try:
            return self._ai_queue.get_nowait()
        except queue.Empty:
            return None

    @property
    def live_fps(self) -> float:
        if not self._fps_window:
            return 0.0
        avg = sum(self._fps_window) / len(self._fps_window)
        return round(1.0 / avg, 1) if avg > 0 else 0.0

    def stats(self) -> dict:
        elapsed = (self._record_stopped_at or time.time()) - self._record_started_at
        return {
            "role": self.role,
            "label": self.config.label,
            "camera_index": self.config.index,
            "status": self.status,
            "error": self.error,
            "enabled": self.config.enabled,
            "recording": self._recording,
            "video_id": self.video_id,
            "filename": self.filename,
            "video_path": self.video_path,
            "codec": self.codec,
            "resolution": f"{self._output_size()[0]}x{self._output_size()[1]}",
            "live_fps": self.live_fps,
            "measured_fps": self.measured_fps,
            "reported_fps": self.reported_fps,
            "frames_read": self.frames_read,
            "frames_written": self.frames_written,
            "read_failures": self.read_failures,
            "reopen_count": self.reopen_count,
            "ai_frames_dropped": self.frames_dropped_for_ai,
            "elapsed_seconds": round(max(0.0, elapsed), 2),
        }

    # -- shutdown ----------------------------------------------------------

    def stop_recording(self, keep_capture=None) -> dict:
        with self._lifecycle_lock:
            return self._stop_recording_locked(keep_capture)

    def _stop_recording_locked(self, keep_capture=None) -> dict:
        """Stop the thread and finalize this channel's video file."""
        keep_capture = (self.keep_capture_alive or self.preview_ai_enabled) if keep_capture is None else keep_capture
        if not keep_capture:
            self._stop_event.set()
            thread = self._thread
            if thread is not None and thread.is_alive():
                thread.join(timeout=5.0)
            if thread is None or not thread.is_alive():
                self._thread = None
        if self._recording or not self._record_stopped_at:
            self._record_stopped_at = time.time()

        duration = max(0.001, self._record_stopped_at - self._record_started_at)
        true_fps = round(self.frames_written / duration, 2) if self.frames_written else 0.0

        with self._write_lock:
            self._recording = False
            if self._writer is not None:
                try:
                    self._writer.release()
                except Exception as exc:  # pragma: no cover
                    LOGGER.exception("[%s] writer release failed: %s", self.role, exc)
                self._writer = None

        self._recording = False
        if self.status == STATUS_RECORDING:
            self.status = STATUS_LIVE

        result = self.stats()
        result.update(
            {
                "duration_seconds": round(duration, 2),
                "true_fps": true_fps,
                "writer_fps": self.measured_fps,
                # Post-processing should re-time the video with true_fps when
                # it differs from writer_fps by more than ~10%.
                "needs_retiming": bool(
                    true_fps and self.measured_fps and abs(true_fps - self.measured_fps) / self.measured_fps > 0.10
                ),
                "file_exists": bool(self.video_path and os.path.exists(self.video_path)),
                "file_size_bytes": (
                    os.path.getsize(self.video_path)
                    if self.video_path and os.path.exists(self.video_path)
                    else 0
                ),
                "started_at_epoch": self._record_started_at,
                "stopped_at_epoch": self._record_stopped_at,
            }
        )
        return result

    def release(self, force=False) -> None:
        with self._lifecycle_lock:
            self._release_locked(force)

    def _release_locked(self, force=False):
        if self.keep_capture_alive and not force:
            self.stop_recording()
            return
        self.stop_recording(keep_capture=False)
        if self._capture is not None:
            try:
                self._capture.release()
            except Exception:  # pragma: no cover
                pass
        self._capture = None
        if self._thread is not None:
            self._thread.join(timeout=2)
            if not self._thread.is_alive():
                self._thread = None
        with self._frame_lock:
            self._latest_frame = None
        self.status = STATUS_DISABLED if not self.config.enabled else STATUS_IDLE


# --------------------------------------------------------------------------
# Both cameras
# --------------------------------------------------------------------------


class DualCameraManager:
    """Coordinates the front and rear channels under one recording session."""

    def __init__(
        self,
        configs: Sequence[CameraConfig],
        video_dir: str = "data/videos",
        pipeline: Optional[VisionPipeline] = None,
        browser_mode: bool = False,
    ):
        self.video_dir = video_dir
        self.browser_mode = browser_mode
        self.driver_runtime = None
        self._driver_retry_at = 0.0
        self.channels: Dict[str, CameraChannel] = {}
        for config in configs:
            if browser_mode:
                from core.browser_camera import BrowserCameraChannel
                self.channels[config.role] = BrowserCameraChannel(config)
            else:
                from services.driver_monitoring.runtime import shared_driver_channel
                self.channels[config.role] = shared_driver_channel(config) or CameraChannel(config)

        self.session_id: Optional[str] = None
        self.started_at: Optional[str] = None
        self.started_at_epoch: float = 0.0
        self.stopped_at: Optional[str] = None
        self._recording = False
        self._ai_order: List[str] = list(self.channels.keys())
        self._ai_cursor = 0
        self.pipeline = pipeline
        self.latest_event: Optional[Dict[str, object]] = None
        self.latest_annotated_frame: Optional[np.ndarray] = None
        self._preview_results = {}
        self.ai_metrics: Dict[str, float] = {
            "processed_frames": 0.0,
            "processing_time_ms": 0.0,
            "ai_fps": 0.0,
        }
        self._ai_lock = threading.Lock()
        self._preview_worker_lock = threading.Lock()
        self._preview_worker = None
        self._preview_generation = 0
        self._ai_started_at = time.monotonic()

    @classmethod
    def from_settings(cls, settings: Optional[dict] = None, video_dir: str = "data/videos") -> "DualCameraManager":
        from core.capture_mode import browser_capture_enabled
        if browser_capture_enabled(settings):
            settings = settings or {}
            configs = [CameraConfig(index=i, role=role, label=label, width=640, height=480,
                                    target_fps=15, ai_sample_interval=.5, mirror=False, enabled=role == REAR)
                       for i, role, label in ((0, FRONT, "Browser · Main"), (1, REAR, "Browser · Cabin"))]
            return cls(configs, video_dir=video_dir, browser_mode=True)
        manager = cls(camera_configs_from_settings(settings), video_dir=video_dir)
        settings = settings or {}
        value = settings.get("droidcam_fallback_enabled", settings.get("DROIDCAM_FALLBACK_ENABLED", os.getenv("DROIDCAM_FALLBACK_ENABLED", "true")))
        manager.droidcam_fallback_enabled = str(value).lower() in {"true", "1", "yes", "on"}
        return manager

    # -- properties --------------------------------------------------------

    @property
    def is_recording(self) -> bool:
        return self._recording

    def channel(self, role: str) -> Optional[CameraChannel]:
        return self.channels.get(role)

    def ensure_browser_driver(self):
        """Session-scoped consumer; never invoke the process-global camera owner."""
        if not self.browser_mode or not self.channel(REAR).browser_source.ready:
            return
        runtime = self.driver_runtime
        if runtime is not None:
            if not runtime._closed or any(t and t.is_alive() for t in (runtime._thread, runtime._producer_thread)):
                return
        if time.monotonic() < self._driver_retry_at:
            return
        try:
            from services.driver_monitoring.runtime import DriverMonitoringRuntime
            from services.driver_monitoring.config import MonitoringConfig
            runtime = DriverMonitoringRuntime(MonitoringConfig.from_env())
            runtime.source_idle_timeout = 15
            runtime.start(source=self.channel(REAR).get_latest)
            self.driver_runtime = runtime
            LOGGER.info("[Camera] session driver consumer attached")
        except Exception:
            self._driver_retry_at = time.monotonic() + 30
            LOGGER.exception("[Camera] driver initialization failed; capture continues")

    def browser_driver_metadata(self):
        runtime = self.driver_runtime
        if runtime is None:
            return {"driver_id": None, "driver_identity_status": "unknown", "driver_session_id": None}
        state = runtime.snapshot()
        session = runtime.service.sessions.snapshot()
        verified = state["identity_status"] == "verified"
        return {"driver_id": state["driver_id"] if verified else None,
                "driver_identity_status": "verified" if verified else "unknown",
                "driver_session_id": session["session_id"]}

    @property
    def preview_active(self) -> bool:
        return any(channel.preview_ai_enabled and channel.is_live for channel in self.channels.values())

    def start_preview(self) -> bool:
        """Open capture and AI sampling without allocating a recording or writer."""
        self.open()
        for channel in self.channels.values():
            if channel.config.enabled:
                channel.preview_ai_enabled = True
                channel.start()
        return self.preview_active

    def close_preview(self) -> None:
        if self.is_recording:
            raise RuntimeError("Stop recording before closing the preview.")
        self._preview_generation += 1
        if self.driver_runtime is not None:
            self.driver_runtime.stop()
        if self._preview_worker is not None:
            self._preview_worker.join(timeout=0 if self.browser_mode else None)
        for channel in self.channels.values():
            channel.preview_ai_enabled = False
            channel.release()
        with self._ai_lock:
            self._preview_results.clear()

    # -- lifecycle ---------------------------------------------------------

    def open(self) -> Dict[str, bool]:
        """Open every enabled camera. Opens sequentially: simultaneous opens on
        the same USB controller frequently fail on Windows."""
        results: Dict[str, bool] = {}
        for role, channel in self.channels.items():
            results[role] = channel.open()
            time.sleep(0.2)
        if getattr(self, "droidcam_fallback_enabled", False) and sum(results.values()) < 2:
            source = droidcam_index()
            front = self.channels.get(FRONT)
            in_use = any(results.get(role) and channel.config.index == source
                         for role, channel in self.channels.items() if role != FRONT)
            if source is not None and front is not None and not in_use and front.config.index != source:
                from dataclasses import replace
                fallback = CameraChannel(replace(front.config, index=source, label="DroidCam", backend=_default_backend()))
                if fallback.open():
                    front.release()
                    self.channels[FRONT] = fallback
                    results[FRONT] = True
        opened = [r for r, ok in results.items() if ok]
        if not opened:
            LOGGER.error("No cameras opened: %s", {r: c.error for r, c in self.channels.items()})
        elif len(opened) < len([c for c in self.channels.values() if c.config.enabled]):
            LOGGER.warning("Running with a partial rig: %s", opened)
        return results

    def calibrate(self, seconds: float = 1.5) -> Dict[str, float]:
        """Measure each camera's real throughput while both are streaming.

        A camera opened alone reports a rate it cannot sustain once its
        neighbour is also pulling frames over the same USB controller. Writing
        at that optimistic rate is what makes saved video play too fast, so the
        writer fps comes from this concurrent measurement instead.
        """
        channels = [c for c in self.channels.values() if c.config.enabled and c._capture is not None and not c.is_live]
        if len(channels) < 2:
            return {c.role: c.measured_fps for c in channels}

        counts: Dict[str, int] = {c.role: 0 for c in channels}
        stop_at = time.time() + seconds

        def pull(channel: CameraChannel) -> None:
            while time.time() < stop_at:
                ok, frame = channel._capture.read()
                if ok and frame is not None:
                    counts[channel.role] += 1

        threads = [threading.Thread(target=pull, args=(c,), daemon=True) for c in channels]
        started = time.time()
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=seconds + 2)
        elapsed = max(0.001, time.time() - started)

        results = {}
        for channel in channels:
            measured = round(counts[channel.role] / elapsed, 2)
            if measured >= 1:
                channel.measured_fps = min(measured, float(channel.config.target_fps))
            results[channel.role] = channel.measured_fps
            LOGGER.info("[%s] calibrated to %.2f fps under dual load", channel.role, channel.measured_fps)
        return results

    def start_recording(self, session_id: Optional[str] = None) -> dict:
        """Start both channels on one shared session.

        Each camera gets its own video_id and its own file so existing
        single-video metadata, playback, and sync keep working unchanged.
        """
        if self._recording:
            return self.session_summary()

        self._preview_generation += 1
        if self._preview_worker is not None:
            self._preview_worker.join(timeout=0 if self.browser_mode else None)

        with self._ai_lock:
            self._preview_results.clear()

        from services.driver_monitoring.runtime import identity_metadata
        self._driver_start_metadata = self.browser_driver_metadata() if self.browser_mode else identity_metadata()
        self._driver_observations = {}
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        readable = datetime.now().strftime("%Y_%m_%d_%H%M%S")
        short = _new_short_id()
        self.session_id = session_id or f"ses_{stamp}_{short}"
        self.started_at = utc_now()
        self.started_at_epoch = time.time()
        self.stopped_at = None

        os.makedirs(self.video_dir, exist_ok=True)
        self.open()
        self.calibrate()
        started_any = False

        for role, channel in self.channels.items():
            if not channel.config.enabled:
                continue
            video_id = f"vid_{stamp}_{role}_{short}"
            filename = f"recording_{readable}_{role}.mp4"
            path = os.path.join(self.video_dir, filename)
            ok = channel.start(video_path=path, video_id=video_id)
            started_any = started_any or (ok and channel.is_recording)
            if not ok:
                LOGGER.error("[%s] failed to start: %s", role, channel.error)

        self._recording = started_any
        if not started_any:
            LOGGER.error("Session %s could not start any camera", self.session_id)
        session = self.stop_recording() if not started_any else {
            **self._driver_start_metadata,
            "session_id": self.session_id,
            "session_type": "dual",
            "started_at": self.started_at,
            "ended_at": None,
            "duration_seconds": 0.0,
            "cameras": [ch.stats() for ch in self.channels.values()],
            "camera_offsets_seconds": {},
            "primary_video_id": next(
                (
                    ch.video_id for ch in self.channels.values() if ch.role == FRONT and ch.video_id
                ),
                next((ch.video_id for ch in self.channels.values() if ch.video_id), None),
            ),
            "video_ids": [ch.video_id for ch in self.channels.values() if ch.video_id],
            "recording": self._recording,
        }
        from services.driver_monitoring.runtime import recording_context
        if not self.browser_mode:
            recording_context(session.get("primary_video_id"))
        elif self.driver_runtime is not None:
            self.driver_runtime.service.recording_context(session.get("primary_video_id"))
        return session

    def stop_recording(self) -> dict:
        """Stop both channels and return the finalized session record."""
        self._preview_generation += 1
        if self._preview_worker is not None:
            self._preview_worker.join(timeout=0 if self.browser_mode else None)
        from services.driver_monitoring.runtime import recording_context
        if not self.browser_mode:
            recording_context()
        elif self.driver_runtime is not None:
            self.driver_runtime.service.recording_context()
        if not self.session_id:
            return {}

        camera_results = []
        for _, channel in self.channels.items():
            if not channel.config.enabled:
                continue
            camera_results.append(channel.stop_recording())

        self._recording = False
        self.stopped_at = utc_now()
        duration = round(max(0.0, time.time() - self.started_at_epoch), 2)

        # Offset between the two cameras' first written frames, used later to
        # align the side-by-side composite.
        offsets = {}
        base = min((c.get("started_at_epoch", 0) for c in camera_results if c.get("started_at_epoch")), default=0)
        for cam in camera_results:
            offsets[cam["role"]] = round(max(0.0, cam.get("started_at_epoch", base) - base), 3)

        session = {
            **getattr(self, "_driver_start_metadata", {}),
            "driver_observations": getattr(self, "_driver_observations", {}),
            "session_id": self.session_id,
            "session_type": "dual" if len(camera_results) > 1 else "single",
            "started_at": self.started_at,
            "ended_at": self.stopped_at,
            "duration_seconds": duration,
            "cameras": camera_results,
            "camera_offsets_seconds": offsets,
            "primary_video_id": next(
                (c["video_id"] for c in camera_results if c["role"] == FRONT and c.get("video_id")),
                camera_results[0]["video_id"] if camera_results else None,
            ),
            "video_ids": [c["video_id"] for c in camera_results if c.get("video_id")],
        }
        return session

    def release(self) -> None:
        self._preview_generation += 1
        if self.driver_runtime is not None:
            self.driver_runtime.stop()
        if self._preview_worker is not None:
            self._preview_worker.join(timeout=0 if self.browser_mode else None)
        for channel in self.channels.values():
            channel.release()
        self._recording = False

    def __enter__(self) -> "DualCameraManager":
        self.open()
        return self

    def __exit__(self, *exc_info) -> None:
        del exc_info
        self.release()

    # -- consumers ---------------------------------------------------------

    def previews(self) -> Dict[str, Optional[np.ndarray]]:
        return {role: ch.get_preview() for role, ch in self.channels.items()}

    def composite_preview(self, layout: str = "side_by_side", height: int = 540) -> np.ndarray:
        """One frame for the UI. layout: side_by_side | pip_front | pip_rear |
        front_only | rear_only."""
        frames = self.previews()
        front_ch, rear_ch = self.channels.get(FRONT), self.channels.get(REAR)
        front_label = front_ch.config.label if front_ch else "Front"
        rear_label = rear_ch.config.label if rear_ch else "Rear"
        front, rear = frames.get(FRONT), frames.get(REAR)

        if layout == "front_only":
            return _stamp_label(front, front_label) if front is not None else placeholder_frame(960, height, front_label + " offline")
        if layout == "rear_only":
            return _stamp_label(rear, rear_label) if rear is not None else placeholder_frame(960, height, rear_label + " offline")
        if layout == "pip_front":
            return compose_pip(front, rear, main_label=front_label, inset_label=rear_label)
        if layout == "pip_rear":
            return compose_pip(rear, front, main_label=rear_label, inset_label=front_label)
        return compose_side_by_side(front, rear, height=height, left_label=front_label, right_label=rear_label)

    def detection_preview(self, role: str = FRONT, max_age_seconds: float = 2.0):
        """Show boxes on their exact source frame, never on the other camera."""
        if role == "both":
            front, front_event = self.detection_preview(FRONT, max_age_seconds)
            rear, rear_event = self.detection_preview(REAR, max_age_seconds)
            labels = {key: self.channels[key].config.label if key in self.channels else key.title()
                      for key in (FRONT, REAR)}
            frame = compose_side_by_side(front, rear, height=480,
                                        left_label="Main · " + labels[FRONT],
                                        right_label="Cabin · " + labels[REAR])
            events = (front_event, rear_event)
            event = {
                "camera_role": "both",
                "objects": [{**item, "camera_role": source["camera_role"]}
                            for source in events for item in source.get("objects", [])],
                "plates": [{**item, "camera_role": source["camera_role"]}
                           for source in events for item in source.get("plates", [])],
                "people_count": sum(source.get("people_count", 0) for source in events),
                "vehicle_count": sum(source.get("vehicle_count", 0) for source in events),
                "movement_detected": any(source.get("movement_detected", False) for source in events),
                "plate_text": ", ".join(source["plate_text"] for source in events if source.get("plate_text")),
            }
            return frame, event
        channel = self.channels.get(role)
        label = channel.config.label if channel else role.title()
        with self._ai_lock:
            result = self._preview_results.get(role)
            if result is not None and time.monotonic() - result[2] <= max_age_seconds:
                return result[0].copy(), deepcopy(result[1])
        frame = channel.get_preview() if channel else None
        if frame is None:
            frame = placeholder_frame(960, 540, label + " offline")
        return frame, {"camera_role": role, "objects": [], "plates": []}

    def _handle_ai_frame(self, role: str, frame: np.ndarray, frame_number: int, timestamp: float):
        generation = self._preview_generation
        from services.driver_monitoring.runtime import identity_metadata
        driver_context = self.browser_driver_metadata() if self.browser_mode else identity_metadata()
        if self.pipeline is None:
            return frame, {"frame_number": frame_number, "camera_role": role, "objects": [], "movement_detected": False}
        started = time.monotonic()
        try:
            camera_context = {"camera_role": role} if isinstance(self.pipeline, VisionPipeline) else {}
            annotated, event = self.pipeline.process(
                frame, frame_number, 0.0, media_timestamp_seconds=timestamp, **camera_context
            )
        except TypeError:
            annotated, event = self.pipeline.process(frame, frame_number, 0.0)
        except Exception as exc:  # pragma: no cover - pipeline dependent
            annotated = frame
            event = {"frame_number": frame_number, "camera_role": role, "objects": [], "movement_detected": False, "error": str(exc)}
        event = dict(event or {})
        event.update(driver_context)
        event.setdefault("frame_number", frame_number)
        event["camera_role"] = role
        event.setdefault("objects", [])
        event.setdefault("movement_detected", False)
        with self._ai_lock:
            if generation != self._preview_generation:
                return annotated, event
            if self.is_recording and hasattr(self, "_driver_observations"):
                samples = self._driver_observations.setdefault(role, [])
                context = identity_metadata(event)
                if samples and all(samples[-1].get(key) == value for key, value in context.items()) and timestamp - samples[-1]["observed_at"] <= 1:
                    samples[-1].update(end_frame=frame_number, observed_at=timestamp)
                else:
                    samples.append({**context, "frame_number": frame_number, "end_frame": frame_number, "observed_at": timestamp})
            self.latest_event = event
            self.latest_annotated_frame = annotated
            self._preview_results[role] = (annotated.copy(), deepcopy(event), time.monotonic())
            self.ai_metrics["processed_frames"] += 1
            self.ai_metrics["processing_time_ms"] = (time.monotonic() - started) * 1000
            self.ai_metrics["ai_fps"] = self.ai_metrics["processed_frames"] / max(time.monotonic() - self._ai_started_at, 0.001)
        return annotated, event

    def next_ai_frame(self) -> Optional[Tuple[str, np.ndarray, int, float]]:
        """Round-robin across channels so one camera cannot starve the other."""
        for _ in range(len(self._ai_order)):
            role = self._ai_order[self._ai_cursor % len(self._ai_order)]
            self._ai_cursor += 1
            channel = self.channels.get(role)
            if channel is None or not channel.config.ai_enabled:
                continue
            item = channel.get_ai_frame()
            if item is not None:
                return item
        return None

    def drain_ai_frames(self, limit: int = 2) -> List[Tuple[str, np.ndarray, int, float]]:
        items = []
        for _ in range(limit):
            item = self.next_ai_frame()
            if item is None:
                break
            items.append(item)
        return items

    def status(self) -> Dict[str, dict]:
        return {role: ch.stats() for role, ch in self.channels.items()}

    def process_live_ai(self, limit: int = 2) -> List[dict]:
        results = []
        for role, frame, frame_number, timestamp in self.drain_ai_frames(limit):
            _, event = self._handle_ai_frame(role, frame, frame_number, timestamp)
            results.append(event)
        return results

    def process_preview_ai_async(self):
        """At most one inference batch; UI never waits or queues inference jobs."""
        with self._preview_worker_lock:
            if self._preview_worker is not None and self._preview_worker.is_alive():
                return
            def work():
                try:
                    self.process_live_ai(limit=2)
                except Exception:
                    LOGGER.exception("Preview inference failed")
            self._preview_worker = threading.Thread(target=work, name="preview-inference", daemon=True)
            self._preview_worker.start()

    def session_summary(self) -> dict:
        return {
            "session_id": self.session_id,
            "started_at": self.started_at,
            "recording": self._recording,
            "cameras": [ch.stats() for ch in self.channels.values()],
        }
