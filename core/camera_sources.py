"""Discover Windows DirectShow sources without opening a camera."""
import platform
import re
import subprocess
import time

_cached = (0.0, None)


def windows_camera_devices():
    global _cached
    if platform.system() != "Windows":
        return None
    if time.monotonic() - _cached[0] < 30:
        return _cached[1]
    try:
        import imageio_ffmpeg
        result = subprocess.run(
            [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
            capture_output=True, text=True, errors="replace", timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        devices = parse_devices(result.stderr)
    except (ImportError, OSError, subprocess.TimeoutExpired):
        devices = None
    _cached = (time.monotonic(), devices)
    return devices


def parse_devices(output):
    # DirectShow's device ordinal includes video devices with no supported format.
    entries = re.findall(r'\] "([^"]+)" \((video|none)\)', output)
    return [(index, name, kind == "video") for index, (name, kind) in enumerate(entries)]


def droidcam_index():
    return next((index for index, name, usable in windows_camera_devices() or []
                 if usable and "droidcam" in name.lower()), None)
