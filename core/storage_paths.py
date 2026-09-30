"""Runtime media storage; cloud staging is deliberately ephemeral."""
import os
import platform
import tempfile
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]


def cloud_runtime():
    mode = os.getenv("ROADWATCH_STORAGE_MODE", "auto").lower()
    if mode not in {"auto", "local", "temporary"}:
        raise ValueError("ROADWATCH_STORAGE_MODE must be auto, local or temporary")
    return mode == "temporary" or (mode == "auto" and platform.system() == "Linux"
                                    and not any(Path("/dev").glob("video*")))


def data_directory():
    return Path(tempfile.gettempdir()) / "roadwatch" / "data" if cloud_runtime() else PROJECT_DIR / "data"


DATA_DIR = data_directory()
