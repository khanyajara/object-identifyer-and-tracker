"""Compression, thumbnail, and failed-export cleanup for recorded video."""

from pathlib import Path

import cv2

from core.video_io import compress_video_for_playback, is_playable_video_path


class CompressionService:
    def compress_for_playback(self, source_path, target_path):
        """Create and validate a browser-friendly MP4."""
        result = compress_video_for_playback(source_path, target_path)
        if result["ok"] and not is_playable_video_path(result["path"]):
            return {"ok": False, "path": Path(source_path), "message": "Compressed output is not playable. Using saved fallback video.", "error": "Compressed output could not be opened by OpenCV."}
        return result

    def create_thumbnail(self, source_path, target_path):
        source_path = Path(source_path)
        target_path = Path(target_path).with_suffix(".jpg")
        if not is_playable_video_path(source_path):
            return {"ok": False, "path": None, "error": "Video source is not playable."}
        capture = cv2.VideoCapture(str(source_path))
        try:
            frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            if frame_count > 1:
                capture.set(cv2.CAP_PROP_POS_FRAMES, frame_count // 2)
            ok, frame = capture.read()
            if not ok or frame is None:
                return {"ok": False, "path": None, "error": "Could not read a thumbnail frame."}
            height, width = frame.shape[:2]
            if width > 640:
                frame = cv2.resize(frame, (640, max(1, int(height * 640 / width))))
            target_path.parent.mkdir(parents=True, exist_ok=True)
            if not cv2.imwrite(str(target_path), frame):
                return {"ok": False, "path": None, "error": "Could not write thumbnail image."}
            return {"ok": True, "path": target_path, "error": None}
        finally:
            capture.release()

    def cleanup_failed_exports(self, exports_dir, dry_run=False):
        """Remove only known failed conversion artifacts; never touch media exports."""
        removed, skipped = [], []
        for path in Path(exports_dir).glob("*"):
            if not path.is_file() or path.name == ".gitkeep":
                continue
            failed_temporary = path.suffix in {".tmp", ".part", ".raw"}
            failed_empty_stem = not path.suffix and path.stat().st_size <= 4
            if not (failed_temporary or failed_empty_stem):
                skipped.append(path)
                continue
            removed.append(path)
            if not dry_run:
                path.unlink()
        return {"removed": removed, "skipped": skipped}
