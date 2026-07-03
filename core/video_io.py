import shutil
import subprocess
from pathlib import Path

import cv2


DEFAULT_VIDEO_EXTENSION = ".mp4"
SUPPORTED_VIDEO_EXTENSIONS = (".webm", ".avi", ".mp4")
VIDEO_WRITER_CANDIDATES = (
    (".mp4", "mp4v"),
    (".avi", "XVID"),
    (".avi", "MJPG"),
)
MP4_CODECS = ("avc1", "H264", "mp4v")


def video_mime_type(path):
    suffix = Path(path).suffix.lower()
    if suffix == ".webm":
        return "video/webm"
    if suffix == ".mp4":
        return "video/mp4"
    if suffix == ".avi":
        return "video/x-msvideo"
    return "application/octet-stream"


def create_video_writer(path, fps, size):
    path = Path(path)
    for extension, codec in VIDEO_WRITER_CANDIDATES:
        output_path = path.with_suffix(extension)
        writer = cv2.VideoWriter(
            str(output_path),
            cv2.VideoWriter_fourcc(*codec),
            fps,
            size,
        )
        if writer.isOpened():
            return writer, output_path, codec
        writer.release()
    raise RuntimeError("No supported video writer codec found.")


def open_video_writer(path, fps, size):
    return create_video_writer(path, fps, size)


def open_webm_writer(path, fps, size):
    return create_video_writer(path, fps, size)


def open_mp4_writer(path, fps, size):
    for codec in MP4_CODECS:
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
        # OneDrive can briefly lock completed temporary video files.
        shutil.copyfile(source_path, target_path)
        try:
            source_path.unlink()
        except OSError:
            pass


def convert_to_webm(source_path, target_path):
    source_path = Path(source_path)
    target_path = Path(target_path).with_suffix(".webm")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return {
            "ok": False,
            "path": source_path,
            "message": "WebM conversion unavailable. Saved compatible fallback video.",
        }
    target_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg,
        "-y",
        "-i",
        str(source_path),
        "-c:v",
        "libvpx",
        "-b:v",
        "2M",
        "-an",
        str(target_path),
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "ok": False,
            "path": source_path,
            "message": f"WebM conversion unavailable. Saved compatible fallback video. {exc}",
        }
    if result.returncode != 0 or not target_path.exists() or not target_path.stat().st_size:
        return {
            "ok": False,
            "path": source_path,
            "message": "WebM conversion unavailable. Saved compatible fallback video.",
            "ffmpeg_error": (result.stderr or result.stdout or "").strip()[-800:],
        }
    return {
        "ok": True,
        "path": target_path,
        "message": "WebM conversion complete.",
    }


def compress_video_for_playback(source_path, target_path):
    source_path = Path(source_path)
    target_path = Path(target_path).with_suffix(".mp4")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return {
            "ok": False,
            "path": source_path,
            "message": "FFmpeg compression unavailable. Using saved fallback video.",
            "error": "ffmpeg not found on PATH",
        }
    target_path.parent.mkdir(parents=True, exist_ok=True)
    commands = [
        [
            ffmpeg,
            "-y",
            "-i",
            str(source_path),
            "-vcodec",
            "libx264",
            "-crf",
            "28",
            "-preset",
            "veryfast",
            "-movflags",
            "+faststart",
            "-an",
            str(target_path),
        ],
        [
            ffmpeg,
            "-y",
            "-i",
            str(source_path),
            "-vcodec",
            "mpeg4",
            "-q:v",
            "5",
            "-an",
            str(target_path),
        ],
    ]
    last_error = ""
    for command in commands:
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=600,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            last_error = str(exc)
            continue
        if result.returncode == 0 and target_path.exists() and target_path.stat().st_size:
            return {
                "ok": True,
                "path": target_path,
                "message": "Video compression complete.",
                "codec": command[command.index("-vcodec") + 1],
            }
        last_error = (result.stderr or result.stdout or "").strip()[-800:]
    return {
        "ok": False,
        "path": source_path,
        "message": "Video compression failed. Using saved fallback video.",
        "error": last_error,
    }


def remove_file_quietly(path):
    try:
        Path(path).unlink()
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
