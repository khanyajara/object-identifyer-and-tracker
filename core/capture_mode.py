"""Select browser transport on servers without camera hardware."""
import os
import platform
from pathlib import Path


def browser_capture_enabled(settings=None):
    settings = settings or {}
    legacy = settings.get("camera_input_mode", settings.get("CAMERA_INPUT_MODE", os.getenv("CAMERA_INPUT_MODE", "auto")))
    mode = settings.get("roadwatch_camera_backend", settings.get("ROADWATCH_CAMERA_BACKEND", os.getenv("ROADWATCH_CAMERA_BACKEND") or legacy))
    mode = str(mode).strip().lower()
    if mode not in {"auto", "browser", "local", "opencv"}:
        raise ValueError("ROADWATCH_CAMERA_BACKEND must be auto, browser or opencv")
    if mode != "auto":
        return mode == "browser"
    return platform.system() == "Linux" and not any(Path("/dev").glob("video*"))
