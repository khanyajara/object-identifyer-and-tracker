"""
core/dual_camera.py

Dual camera capture for Roadwatch Vision Recorder.

Design rules carried over from the single-camera recorder:

    Record every camera frame.
    Process AI without blocking recording.
    Save detections under the video they belong to.

Each camera is an independent channel with its own OpenCV capture, its own
background thread, and its own WebM writer. One camera failing (unplugged,
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
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple

from core.vision_pipeline import VisionPipeline

import cv2
import numpy as np

LOGGER = logging.getLogger("roadwatch.dual_camera")

FRONT = "front"
REAR = "rear"

# Browser friendly first, legacy fallback second.
_WEBM_CODECS = ("VP80", "VP90")
_MP4_CODECS = ("avc1", "mp4v")

# Status values a channel can report.
STATUS_IDLE = "idle"
STATUS_LIVE = "live"
STATUS_RECORDING = "recording"
STATUS_DEGRADED = "degraded"
STATUS_FAILED = "failed"
STATUS_DISABLED = "disabled"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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

        self._capture: Optional[cv2.VideoCapture] = None
        self._writer: Optional[cv2.VideoWriter] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._frame_lock = threading.Lock()
        self._write_lock = threading.Lock()

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

    def _open_capture(self) -> Optional[cv2.VideoCapture]:
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
                return cap
            cap.release()
        return None

    def _measure_fps(self, cap: cv2.VideoCapture, sample_frames: int = 20) -> float:
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
                self.error = "No usable video codec (tried VP9 then VP8). Recording preview only."
                LOGGER.error("[%s] %s", self.role, self.error)
            else:
                self._writer = writer
                self.codec = codec
                self.video_path = video_path
                self.filename = os.path.basename(video_path)
                self.video_id = video_id
                self._recording = True
                self.status = STATUS_RECORDING

        self.frames_read = 0
        self.frames_written = 0
        self.frames_dropped_for_ai = 0
        self.read_failures = 0
        self._fps_window = []
        self._record_started_at = time.time()
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
            if self.config.ai_enabled and (now - self._last_ai_push) >= self.config.ai_sample_interval:
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

    def stop_recording(self) -> dict:
        """Stop the thread and finalize this channel's video file."""
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)
        self._thread = None
        self._record_stopped_at = time.time()

        duration = max(0.001, self._record_stopped_at - self._record_started_at)
        true_fps = round(self.frames_written / duration, 2) if self.frames_written else 0.0

        with self._write_lock:
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

    def release(self) -> None:
        self.stop_recording()
        if self._capture is not None:
            try:
                self._capture.release()
            except Exception:  # pragma: no cover
                pass
        self._capture = None
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
    ):
        self.video_dir = video_dir
        self.channels: Dict[str, CameraChannel] = {}
        for config in configs:
            self.channels[config.role] = CameraChannel(config)

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
        self.ai_metrics: Dict[str, float] = {
            "processed_frames": 0.0,
            "processing_time_ms": 0.0,
            "ai_fps": 0.0,
        }
        self._ai_lock = threading.Lock()
        self._ai_started_at = time.monotonic()

    @classmethod
    def from_settings(cls, settings: Optional[dict] = None, video_dir: str = "data/videos") -> "DualCameraManager":
        return cls(camera_configs_from_settings(settings), video_dir=video_dir)

    # -- properties --------------------------------------------------------

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def roles(self) -> List[str]:
        return list(self.channels.keys())

    @property
    def active_roles(self) -> List[str]:
        return [role for role, ch in self.channels.items() if ch.is_live]

    def channel(self, role: str) -> Optional[CameraChannel]:
        return self.channels.get(role)

    # -- lifecycle ---------------------------------------------------------

    def open(self) -> Dict[str, bool]:
        """Open every enabled camera. Opens sequentially: simultaneous opens on
        the same USB controller frequently fail on Windows."""
        results: Dict[str, bool] = {}
        for role, channel in self.channels.items():
            results[role] = channel.open()
            time.sleep(0.2)
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

    def start_preview(self) -> Dict[str, bool]:
        """Live view without recording."""
        return {role: ch.start() for role, ch in self.channels.items() if ch.config.enabled}

    def start_recording(self, session_id: Optional[str] = None) -> dict:
        """Start both channels on one shared session.

        Each camera gets its own video_id and its own file so existing
        single-video metadata, playback, and sync keep working unchanged.
        """
        if self._recording:
            return self.session_summary()

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        readable = datetime.now().strftime("%Y_%m_%d_%H%M%S")
        short = _new_short_id()
        self.session_id = session_id or f"ses_{stamp}_{short}"
        self.started_at = _utc_now_iso()
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
            filename = f"recording_{readable}_{role}.webm"
            path = os.path.join(self.video_dir, filename)
            ok = channel.start(video_path=path, video_id=video_id)
            started_any = started_any or ok
            if not ok:
                LOGGER.error("[%s] failed to start: %s", role, channel.error)

        self._recording = started_any
        if not started_any:
            LOGGER.error("Session %s could not start any camera", self.session_id)
        session = self.stop_recording() if not started_any else {
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
        return session

    def stop_recording(self) -> dict:
        """Stop both channels and return the finalized session record."""
        if not self.session_id:
            return {}

        camera_results = []
        for _, channel in self.channels.items():
            if not channel.config.enabled:
                continue
            camera_results.append(channel.stop_recording())

        self._recording = False
        self.stopped_at = _utc_now_iso()
        duration = round(max(0.0, time.time() - self.started_at_epoch), 2)

        # Offset between the two cameras' first written frames, used later to
        # align the side-by-side composite.
        offsets = {}
        base = min((c.get("started_at_epoch", 0) for c in camera_results if c.get("started_at_epoch")), default=0)
        for cam in camera_results:
            offsets[cam["role"]] = round(max(0.0, cam.get("started_at_epoch", base) - base), 3)

        session = {
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

    def _handle_ai_frame(self, role: str, frame: np.ndarray, frame_number: int, timestamp: float):
        if self.pipeline is None:
            return frame, {"frame_number": frame_number, "camera_role": role, "objects": [], "movement_detected": False}
        started = time.monotonic()
        try:
            annotated, event = self.pipeline.process(frame, frame_number, 0.0, media_timestamp_seconds=timestamp)
        except TypeError:
            annotated, event = self.pipeline.process(frame, frame_number, 0.0)
        except Exception as exc:  # pragma: no cover - pipeline dependent
            annotated = frame
            event = {"frame_number": frame_number, "camera_role": role, "objects": [], "movement_detected": False, "error": str(exc)}
        event = dict(event or {})
        event.setdefault("frame_number", frame_number)
        event.setdefault("camera_role", role)
        event.setdefault("objects", [])
        event.setdefault("movement_detected", False)
        with self._ai_lock:
            self.latest_event = event
            self.latest_annotated_frame = annotated
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
        for _ in range(limit):
            item = self.next_ai_frame()
            if item is None:
                break
            role, frame, frame_number, timestamp = item
            _, event = self._handle_ai_frame(role, frame, frame_number, timestamp)
            results.append(event)
        return results

    def session_summary(self) -> dict:
        return {
            "session_id": self.session_id,
            "started_at": self.started_at,
            "recording": self._recording,
            "cameras": [ch.stats() for ch in self.channels.values()],
        }

    def health_messages(self) -> List[str]:
        messages = []
        for _, channel in self.channels.items():
            if not channel.config.enabled:
                continue
            if channel.status in (STATUS_FAILED, STATUS_DEGRADED) and channel.error:
                messages.append(f"{channel.config.label}: {channel.error}")
            elif channel.is_recording and channel.live_fps and channel.live_fps < channel.config.target_fps * 0.5:
                messages.append(
                    f"{channel.config.label} is capturing at {channel.live_fps} fps against a target of "
                    f"{channel.config.target_fps}. Lower the resolution or move one camera to another USB controller."
                )
        return messages


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------


def probe_cameras(max_index: int = 6, backend: Optional[int] = None) -> List[dict]:
    """List camera indices that deliver a frame. Used by Settings and tools."""
    backend = backend if backend is not None else _default_backend()
    found = []
    for index in range(max_index):
        cap = cv2.VideoCapture(index, backend)
        if cap.isOpened():
            ok, frame = cap.read()
            if ok and frame is not None:
                found.append(
                    {
                        "index": index,
                        "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                        "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                        "fps": round(float(cap.get(cv2.CAP_PROP_FPS) or 0), 1),
                    }
                )
        cap.release()
    return found


def check_pair(index_a: int, index_b: int, seconds: float = 3.0) -> dict:
    """Open two cameras at once and measure what each actually delivers.

    Two 720p webcams on one USB 2.0 controller will usually fail here. That is
    a bandwidth limit, not a bug in the app.
    """
    backend = _default_backend()
    cap_a = cv2.VideoCapture(index_a, backend)
    cap_b = cv2.VideoCapture(index_b, backend)
    for cap in (cap_a, cap_b):
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

    counts = {index_a: 0, index_b: 0}
    start = time.time()
    while time.time() - start < seconds:
        for index, cap in ((index_a, cap_a), (index_b, cap_b)):
            ok, frame = cap.read()
            if ok and frame is not None:
                counts[index] += 1
    elapsed = max(0.001, time.time() - start)
    cap_a.release()
    cap_b.release()

    return {
        "index_a": index_a,
        "index_b": index_b,
        "fps_a": round(counts[index_a] / elapsed, 1),
        "fps_b": round(counts[index_b] / elapsed, 1),
        "usable": counts[index_a] > 10 and counts[index_b] > 10,
    }
