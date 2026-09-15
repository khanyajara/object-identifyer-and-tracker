"""Select browser transport on servers without camera hardware."""
import os
import platform
from pathlib import Path


def browser_capture_enabled(settings=None):
    settings = settings or {}
    mode = str(settings.get("camera_input_mode", settings.get("CAMERA_INPUT_MODE", os.getenv("CAMERA_INPUT_MODE", "auto")))).lower()
    if mode not in {"auto", "browser", "local"}:
        raise ValueError("CAMERA_INPUT_MODE must be auto, browser or local")
    if mode != "auto":
        return mode == "browser"
    return platform.system() == "Linux" and not any(Path("/dev").glob("video*"))
