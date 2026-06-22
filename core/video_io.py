import shutil
from pathlib import Path

import cv2


def open_mp4_writer(path, fps, size):
    for codec in ("avc1", "H264", "mp4v"):
        writer = cv2.VideoWriter(
            str(path),
            cv2.VideoWriter_fourcc(*codec),
            fps,
            size,
        )
        if writer.isOpened():
            return writer, codec
        writer.release()
    raise RuntimeError("Could not create a browser-playable MP4 writer.")


def finalize_video_file(source_path, target_path):
    source_path = Path(source_path)
    target_path = Path(target_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        source_path.replace(target_path)
    except PermissionError:
        # OneDrive can briefly lock completed temporary MP4s.
        shutil.copyfile(source_path, target_path)
        try:
            source_path.unlink()
        except OSError:
            pass


def is_playable_video_path(path):
    if not path:
        return False
    path = Path(path)
    if not path.exists() or not path.is_file() or not path.stat().st_size:
        return False
    capture = cv2.VideoCapture(str(path))
    try:
        return bool(capture.isOpened() and capture.get(cv2.CAP_PROP_FRAME_COUNT) > 0)
    finally:
        capture.release()
