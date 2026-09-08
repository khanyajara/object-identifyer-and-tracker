"""Validated, portable configuration. No guessed production identity threshold."""
import math
import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class MonitoringConfig:
    enabled: bool = True
    background: bool = True
    recognition_enabled: bool = True
    landmarks_enabled: bool = True
    landmarks_when_idle: bool = False
    detection_fps: float = 5
    no_face_fps: float = 4
    idle_detection_fps: float = 1
    idle_after_seconds: float = 30
    landmark_fps: float = 10
    recognition_interval: float = 2
    candidate_interval: float = 1
    recheck_seconds: float = 7
    index_ttl: float = 300
    recovery_seconds: float = 5
    database_retry_seconds: float = 30
    lost_timeout: float = 5
    required_matches: int = 3
    threshold: float | None = None
    width: int = 640
    height: int = 480
    anchor_x: float = 0.5
    anchor_y: float = 0.5
    seat_radius: float = 0.35
    yunet_path: Path = ROOT / "models" / "face_detection_yunet.onnx"
    sface_path: Path = ROOT / "models" / "face_recognition_sface.onnx"
    landmarker_path: Path = ROOT / "models" / "face_landmarker.task"

    def __post_init__(self):
        for name in ("detection_fps", "no_face_fps", "idle_detection_fps", "idle_after_seconds", "landmark_fps", "recognition_interval", "candidate_interval", "recheck_seconds", "index_ttl", "recovery_seconds", "database_retry_seconds", "lost_timeout", "width", "height", "seat_radius"):
            if not math.isfinite(getattr(self, name)) or getattr(self, name) <= 0:
                raise ValueError("Invalid driver monitoring configuration: " + name)
        if not 2 <= self.required_matches <= 20:
            raise ValueError("At least two independent recognition samples are required.")
        if self.threshold is not None and not (0 < self.threshold <= 1):
            raise ValueError("Calibrated cosine threshold must be in (0, 1].")
        if not (0 <= self.anchor_x <= 1 and 0 <= self.anchor_y <= 1):
            raise ValueError("Driver seat position must be normalized.")
        if self.detection_fps > 30 or self.landmark_fps > 30:
            raise ValueError("Driver sampling rates must not exceed 30 FPS.")
        if self.candidate_interval > self.recognition_interval:
            raise ValueError("Candidate checks must be at least as frequent as unverified checks.")

    @classmethod
    def from_env(cls):
        values = {}
        names = {
            "detection_fps": "DRIVER_FACE_DETECTION_FPS", "landmark_fps": "DRIVER_LANDMARK_FPS",
            "recognition_interval": "FACE_RECOGNITION_INTERVAL_SECONDS", "recheck_seconds": "DRIVER_IDENTITY_RECHECK_SECONDS",
            "lost_timeout": "DRIVER_FACE_LOST_TIMEOUT_SECONDS", "anchor_x": "DRIVER_SEAT_X",
            "anchor_y": "DRIVER_SEAT_Y", "seat_radius": "DRIVER_SEAT_RADIUS",
            "no_face_fps": "DRIVER_NO_FACE_FPS", "idle_detection_fps": "DRIVER_IDLE_DETECTION_FPS",
            "idle_after_seconds": "DRIVER_IDLE_AFTER_SECONDS", "candidate_interval": "FACE_RECOGNITION_CANDIDATE_INTERVAL_SECONDS",
            "index_ttl": "DRIVER_EMBEDDING_INDEX_TTL_SECONDS", "recovery_seconds": "DRIVER_RECOVERY_SECONDS",
            "database_retry_seconds": "DRIVER_DATABASE_RETRY_SECONDS",
        }
        for key, env in names.items():
            if os.getenv(env):
                values[key] = float(os.environ[env])
        for key, env in (("width", "DRIVER_FRAME_WIDTH"), ("height", "DRIVER_FRAME_HEIGHT"), ("required_matches", "FACE_RECOGNITION_REQUIRED_MATCHES")):
            if os.getenv(env):
                values[key] = int(os.environ[env])
        # New names take precedence; legacy deployment names remain compatible.
        for key, env in (("recognition_interval", "FACE_RECOGNITION_UNVERIFIED_INTERVAL_SECONDS"), ("recheck_seconds", "FACE_RECOGNITION_VERIFIED_INTERVAL_SECONDS")):
            if os.getenv(env):
                values[key] = float(os.environ[env])
        for key, env in (("enabled", "DRIVER_MONITORING_ENABLED"), ("background", "DRIVER_MONITORING_BACKGROUND"), ("recognition_enabled", "DRIVER_FACE_RECOGNITION_ENABLED"), ("landmarks_enabled", "DRIVER_LANDMARKS_ENABLED"), ("landmarks_when_idle", "DRIVER_LANDMARKS_WHEN_IDLE")):
            if os.getenv(env):
                values[key] = os.environ[env].lower() in {"true", "1", "yes", "on"}
        if os.getenv("FACE_RECOGNITION_THRESHOLD", "").strip():
            values["threshold"] = float(os.environ["FACE_RECOGNITION_THRESHOLD"])
        for key, env in (("yunet_path", "DRIVER_YUNET_MODEL"), ("sface_path", "DRIVER_SFACE_MODEL"), ("landmarker_path", "DRIVER_LANDMARK_MODEL")):
            if os.getenv(env):
                path = Path(os.environ[env]).expanduser()
                values[key] = path if path.is_absolute() else ROOT / path
        return cls(**values)
