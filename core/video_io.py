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


def _load_moviepy_clip():
    try:
        from moviepy import VideoFileClip
    except ImportError:
        from moviepy.editor import VideoFileClip
    return VideoFileClip


def video_mime_type(path):
    suffix = Path(path).suffix.lower()
    if suffix == ".webm":
        return "video/webm"
    if suffix == ".mp4":
        return "video/mp4"
    if suffix == ".avi":
        return "video/x-msvideo"
    return "application/octet-stream"


def is_remote_video_source(value):
    return isinstance(value, str) and value.lower().startswith(("http://", "https://"))


def _local_video_exists(path):
    if not path or is_remote_video_source(path):
        return False
    path = Path(path)
    return path.exists() and path.is_file() and path.stat().st_size > 0


def get_best_playback_info(video_metadata):
    candidates = (
        ("supabase_webm_url", "webm", "supabase"),
        ("supabase_mp4_url", "mp4", "supabase"),
        ("compressed_processed_path", "mp4", "local"),
        ("processed_mp4_path", "mp4", "local"),
        ("processed_video_path", "mp4", "local"),
        ("compressed_original_path", "mp4", "local"),
        ("original_mp4_path", "mp4", "local"),
        ("original_video_path", "mp4", "local"),
        ("video_path", "mp4", "local"),
    )
    legacy_url_candidates = (
        ("supabase_processed_url", "mp4", "supabase"),
        ("playback_video_url", "mp4", "supabase"),
        ("supabase_url", "mp4", "supabase"),
    )
    for key, video_format, source_type in candidates:
        value = video_metadata.get(key)
        if source_type == "supabase" and is_remote_video_source(value):
            return {
                "source": value,
                "format": video_format,
                "source_type": source_type,
                "metadata_key": key,
            }
        if source_type == "local" and _local_video_exists(value):
            return {
                "source": str(Path(value)),
                "format": Path(value).suffix.lower().lstrip(".") or video_format,
                "source_type": source_type,
                "metadata_key": key,
            }
    for key, video_format, source_type in legacy_url_candidates:
        value = video_metadata.get(key)
        if is_remote_video_source(value):
            return {
                "source": value,
                "format": video_format,
                "source_type": source_type,
                "metadata_key": key,
            }
    return {
        "source": None,
        "format": None,
        "source_type": None,
        "metadata_key": None,
    }


def get_best_playback_source(video_metadata):
    return get_best_playback_info(video_metadata)["source"]


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
    moviepy_result = _convert_to_webm_with_moviepy(source_path, target_path)
    if moviepy_result["ok"]:
        return moviepy_result

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return {
            "ok": False,
            "path": source_path,
            "message": "WebM conversion unavailable. Saved compatible fallback video.",
            "error": moviepy_result.get("error") or "ffmpeg not found on PATH",
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
        "tool": "ffmpeg",
    }


def compress_video_for_playback(source_path, target_path):
    source_path = Path(source_path)
    target_path = Path(target_path).with_suffix(".mp4")
    moviepy_result = _compress_with_moviepy(source_path, target_path)
    if moviepy_result["ok"]:
        return moviepy_result

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return {
            "ok": False,
            "path": source_path,
            "message": "MoviePy/FFmpeg compression unavailable. Using saved fallback video.",
            "error": moviepy_result.get("error") or "ffmpeg not found on PATH",
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
                "tool": "ffmpeg",
            }
        last_error = (result.stderr or result.stdout or "").strip()[-800:]
    return {
        "ok": False,
        "path": source_path,
        "message": "Video compression failed. Using saved fallback video.",
        "error": last_error,
    }


def _compress_with_moviepy(source_path, target_path):
    return _write_moviepy_video(
        source_path,
        target_path,
        codecs=("libx264", "mpeg4"),
        success_message="Video compression complete.",
    )


def _convert_to_webm_with_moviepy(source_path, target_path):
    return _write_moviepy_video(
        source_path,
        target_path,
        codecs=("libvpx",),
        success_message="WebM conversion complete.",
    )


def _write_moviepy_video(source_path, target_path, codecs, success_message):
    source_path = Path(source_path)
    target_path = Path(target_path)
    if not source_path.exists() or not source_path.is_file() or not source_path.stat().st_size:
        return {
            "ok": False,
            "path": source_path,
            "message": "Source video unavailable. Using saved fallback video.",
            "error": f"missing source video: {source_path}",
        }
    target_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        VideoFileClip = _load_moviepy_clip()
    except ImportError as exc:
        return {
            "ok": False,
            "path": source_path,
            "message": "MoviePy is not installed. Using saved fallback video.",
            "error": str(exc),
        }

    last_error = ""
    for codec in codecs:
        clip = None
        try:
            clip = VideoFileClip(str(source_path))
            kwargs = {
                "codec": codec,
                "audio": False,
                "logger": None,
            }
            if target_path.suffix.lower() == ".mp4" and codec == "libx264":
                kwargs["preset"] = "veryfast"
                kwargs["ffmpeg_params"] = ["-crf", "28", "-movflags", "+faststart"]
            elif target_path.suffix.lower() == ".mp4" and codec == "mpeg4":
                kwargs["ffmpeg_params"] = ["-q:v", "5"]
            elif target_path.suffix.lower() == ".webm":
                kwargs["bitrate"] = "2M"
            clip.write_videofile(str(target_path), **kwargs)
            if target_path.exists() and target_path.stat().st_size:
                return {
                    "ok": True,
                    "path": target_path,
                    "message": success_message,
                    "codec": codec,
                    "tool": "moviepy",
                }
            last_error = "MoviePy completed without creating a playable output file."
        except TypeError:
            try:
                if clip is not None:
                    clip.close()
                clip = VideoFileClip(str(source_path))
                clip.write_videofile(
                    str(target_path),
                    codec=codec,
                    audio=False,
                    logger=None,
                )
                if target_path.exists() and target_path.stat().st_size:
                    return {
                        "ok": True,
                        "path": target_path,
                        "message": success_message,
                        "codec": codec,
                        "tool": "moviepy",
                    }
            except Exception as exc:
                last_error = str(exc)
        except Exception as exc:
            last_error = str(exc)
        finally:
            if clip is not None:
                try:
                    clip.close()
                except Exception:
                    pass
    return {
        "ok": False,
        "path": source_path,
        "message": "MoviePy compression failed. Using saved fallback video.",
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
