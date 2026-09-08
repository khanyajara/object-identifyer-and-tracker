"""
services/dual_video_processing.py

Post-processing for dual camera sessions.

Runs after recording stops, never during it. Two jobs:

1. Process each camera's original video into an annotated processed video.
   If the project's own VideoProcessingService is importable it is used, so
   there is one AI pass implementation, not two. Otherwise a built-in pass
   runs VisionPipeline frame by frame.

2. Build one time-aligned side-by-side video from the two processed videos,
   so an incident can be reviewed as a single clip showing both angles.

Cameras are processed sequentially. Running two YOLO passes at once on a
laptop makes both slower and can starve the UI thread.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Callable, Dict, List, Optional, Tuple

import cv2

from core.dual_camera import (
    FRONT,
    REAR,
    compose_side_by_side,
    open_video_writer,
)
from services import dual_session_service

LOGGER = logging.getLogger("roadwatch.dual_processing")

PROCESSED_DIR = os.path.join("data", "videos", "processed")

ProgressCallback = Optional[Callable[[str, float, str], None]]


def _report(callback: ProgressCallback, role: str, fraction: float, message: str) -> None:
    if callback:
        try:
            callback(role, max(0.0, min(1.0, fraction)), message)
        except Exception:  # pragma: no cover - UI callback must never break a job
            LOGGER.debug("progress callback failed", exc_info=True)


def _video_is_readable(path: Optional[str]) -> bool:
    if not path or not os.path.exists(path) or os.path.getsize(path) == 0:
        return False
    cap = cv2.VideoCapture(path)
    ok = cap.isOpened()
    if ok:
        ok, frame = cap.read()
        ok = bool(ok and frame is not None)
    cap.release()
    return ok


def video_is_readable(path: Optional[str]) -> bool:
    """Public alias used by the UI before rendering a player."""
    return _video_is_readable(path)


def processed_path_for(camera: dict) -> str:
    os.makedirs(PROCESSED_DIR, exist_ok=True)
    video_id = camera.get("video_id") or "unknown"
    filename = camera.get("filename") or f"{video_id}.webm"
    stem = os.path.splitext(filename)[0]
    return os.path.join(PROCESSED_DIR, f"{stem}_{video_id}_processed.webm")


def composite_path_for(session: dict) -> str:
    os.makedirs(PROCESSED_DIR, exist_ok=True)
    return os.path.join(PROCESSED_DIR, f"{session.get('session_id', 'session')}_dual.webm")


# --------------------------------------------------------------------------
# Per-camera AI pass
# --------------------------------------------------------------------------


def _external_service():
    """Use the project's existing single-video processor when available."""
    try:
        from services.video_processing_service import VideoProcessingService  # type: ignore

        return VideoProcessingService()
    except Exception:  # pragma: no cover - project dependent
        return None


def _built_in_pass(
    camera: dict,
    pipeline,
    output_path: str,
    output_fps: float,
    progress: ProgressCallback,
) -> Tuple[bool, List[dict], Optional[str]]:
    """Frame by frame AI pass used when VideoProcessingService is not present."""
    source = camera.get("video_path")
    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        return False, [], f"Could not open {source}"

    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    writer, codec = open_video_writer(output_path, output_fps, (width, height))
    if writer is None:
        cap.release()
        return False, [], "No usable codec for the processed video (tried VP9 then VP8)."

    detections: List[dict] = []
    role = camera.get("role")
    frame_number = 0
    started = time.time()

    try:
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            frame_number += 1

            annotated = frame
            if pipeline is not None:
                try:
                    if hasattr(pipeline, "process_frame"):
                        result = pipeline.process_frame(frame)
                    else:
                        result = pipeline.process(
                            frame,
                            frame_number,
                            output_fps,
                            media_timestamp_seconds=(frame_number - 1) / output_fps,
                        )
                    if isinstance(result, tuple):
                        annotated, event = result
                    else:
                        annotated = getattr(result, "annotated_frame", None)
                        event = getattr(result, "detection_event", None)
                        if annotated is None and isinstance(result, dict):
                            annotated = result.get("annotated_frame")
                        if event is None and isinstance(result, dict):
                            event = result.get("detection_event")
                    if annotated is None:
                        annotated = frame
                    if event:
                        event = dict(event)
                        event.update(
                            {
                                "video_id": camera.get("video_id"),
                                "camera_role": role,
                                "frame_number": frame_number,
                            }
                        )
                        detections.append(event)
                except Exception as exc:
                    LOGGER.warning("[%s] frame %s failed: %s", role, frame_number, exc)
                    annotated = frame

            writer.write(annotated)
            if total and frame_number % 15 == 0:
                _report(progress, role, frame_number / total, f"Frame {frame_number} of {total}")
    finally:
        cap.release()
        writer.release()

    LOGGER.info(
        "[%s] processed %s frames in %.1fs with %s",
        role,
        frame_number,
        time.time() - started,
        codec,
    )
    return frame_number > 0, detections, None


def process_camera(
    session: dict,
    camera: dict,
    pipeline=None,
    progress: ProgressCallback = None,
) -> dict:
    """Process one camera. Never raises: failures are recorded on the camera."""
    del session
    role = camera.get("role", "camera")
    result = dict(camera)

    if not _video_is_readable(camera.get("video_path")):
        result["processing_status"] = "Skipped."
        result["processing_error"] = (
            "Original video missing or unreadable. The recording exists in metadata but not on disk."
        )
        return result

    # True fps beats writer fps: two cameras rarely hit the nominal rate, and
    # re-timing here is what stops playback running fast.
    output_fps = camera.get("true_fps") or camera.get("measured_fps") or 20.0
    output_path = processed_path_for(camera)
    _report(progress, role, 0.0, "Starting AI pass")

    service = _external_service()
    if service is not None and hasattr(service, "process_video"):
        try:
            outcome = service.process_video(
                video_path=camera["video_path"],
                video_id=camera.get("video_id"),
                output_path=output_path,
            )
            detections = []
            if isinstance(outcome, dict):
                detections = outcome.get("detections", [])
                output_path = outcome.get("processed_video_path", output_path)
            for event in detections:
                event.setdefault("camera_role", role)
            result.update(
                {
                    "processed_video_path": output_path,
                    "processing_status": "Processed video saved successfully.",
                    "processing_error": None,
                    "detections": detections,
                }
            )
            _report(progress, role, 1.0, "Done")
            return result
        except Exception as exc:
            LOGGER.exception("[%s] VideoProcessingService failed, using built-in pass", role)
            result["processing_error"] = f"VideoProcessingService failed: {exc}"

    ok, detections, error = _built_in_pass(camera, pipeline, output_path, output_fps, progress)
    if ok:
        result.update(
            {
                "processed_video_path": output_path,
                "processing_status": "Processed video saved successfully.",
                "processing_error": None,
                "detections": detections,
            }
        )
        _report(progress, role, 1.0, "Done")
    else:
        result.update(
            {
                "processed_video_path": None,
                "processing_status": "Post-processing failed. The original recording is untouched.",
                "processing_error": error or "Unknown post-processing failure.",
            }
        )
        _report(progress, role, 1.0, "Failed")
    return result


# --------------------------------------------------------------------------
# Composite
# --------------------------------------------------------------------------


def build_composite(
    session: dict,
    prefer_processed: bool = True,
    output_path: Optional[str] = None,
    height: int = 540,
    progress: ProgressCallback = None,
) -> Optional[str]:
    """One clip, both angles, aligned on the recorded start offsets.

    The two cameras start milliseconds apart and run at slightly different
    rates, so frames are paired by elapsed time rather than by frame number.
    """
    cameras = {c.get("role"): c for c in session.get("cameras", []) if c.get("role")}
    front, rear = cameras.get(FRONT), cameras.get(REAR)
    if not front or not rear:
        LOGGER.info("Composite skipped: session %s is single camera", session.get("session_id"))
        return None

    def pick(camera: dict) -> Optional[str]:
        if prefer_processed and _video_is_readable(camera.get("processed_video_path")):
            return camera["processed_video_path"]
        return camera.get("video_path") if _video_is_readable(camera.get("video_path")) else None

    front_src, rear_src = pick(front), pick(rear)
    if not front_src or not rear_src:
        LOGGER.warning("Composite skipped: one side has no readable video")
        return None

    cap_front = cv2.VideoCapture(front_src)
    cap_rear = cv2.VideoCapture(rear_src)
    fps_front = float(front.get("true_fps") or cap_front.get(cv2.CAP_PROP_FPS) or 20)
    fps_rear = float(rear.get("true_fps") or cap_rear.get(cv2.CAP_PROP_FPS) or 20)
    out_fps = max(5.0, min(fps_front, fps_rear))

    offsets = session.get("camera_offsets_seconds", {}) or {}
    front_offset = float(offsets.get(FRONT, 0.0))
    rear_offset = float(offsets.get(REAR, 0.0))

    duration = float(
        session.get("duration_seconds")
        or max(front.get("duration_seconds", 0), rear.get("duration_seconds", 0))
        or 0
    )
    total_out_frames = int(duration * out_fps) if duration else 0

    output_path = output_path or composite_path_for(session)
    writer = None
    front_frame = rear_frame = None
    front_index = rear_index = 0
    written = 0

    try:
        out_index = 0
        while True:
            t = out_index / out_fps

            # Advance each source until its own clock reaches t.
            while front_index / fps_front <= t + front_offset:
                ok, frame = cap_front.read()
                if not ok:
                    front_frame = None
                    break
                front_frame = frame
                front_index += 1

            while rear_index / fps_rear <= t + rear_offset:
                ok, frame = cap_rear.read()
                if not ok:
                    rear_frame = None
                    break
                rear_frame = frame
                rear_index += 1

            if front_frame is None and rear_frame is None:
                break

            tile = compose_side_by_side(
                front_frame,
                rear_frame,
                height=height,
                left_label=front.get("label") or "Front",
                right_label=rear.get("label") or "Rear",
            )

            if writer is None:
                writer, codec = open_video_writer(
                    output_path, out_fps, (tile.shape[1], tile.shape[0])
                )
                if writer is None:
                    LOGGER.error("Composite failed: no usable codec")
                    return None
                LOGGER.info("Composite writing with %s at %.2f fps", codec, out_fps)

            writer.write(tile)
            written += 1
            out_index += 1

            if total_out_frames and out_index % 30 == 0:
                _report(progress, "composite", out_index / total_out_frames, "Building dual view")
            if total_out_frames and out_index > total_out_frames * 1.5:
                break  # safety stop against a bad duration value
    finally:
        cap_front.release()
        cap_rear.release()
        if writer is not None:
            writer.release()

    if written == 0:
        return None
    _report(progress, "composite", 1.0, "Dual view ready")
    LOGGER.info("Composite saved: %s (%s frames)", output_path, written)
    return output_path


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


def process_session(
    session: dict,
    pipeline=None,
    make_composite: bool = True,
    save: bool = True,
    progress: ProgressCallback = None,
) -> dict:
    """Process both cameras, build the composite, persist everything.

    Returns the updated session. Partial success is normal and is recorded per
    camera rather than failing the whole session.
    """
    processed_cameras = []
    detections_by_role: Dict[str, List[dict]] = {}

    for camera in session.get("cameras", []):
        updated = process_camera(session, camera, pipeline=pipeline, progress=progress)
        detections_by_role[updated.get("role", "camera")] = updated.pop("detections", []) or []
        from services.driver_monitoring.metadata import preserve_event_identity
        preserve_event_identity(detections_by_role[updated.get("role", "camera")],
            session.get("driver_observations", {}).get(updated.get("role"), []), float(camera.get("true_fps") or 20))
        processed_cameras.append(updated)

    session = dict(session)
    session["cameras"] = processed_cameras

    if make_composite:
        try:
            composite = build_composite(session, progress=progress)
            session["composite_video_path"] = composite
        except Exception as exc:
            LOGGER.exception("Composite failed: %s", exc)
            session["composite_video_path"] = None
            session["composite_error"] = str(exc)

    failures = [c for c in processed_cameras if c.get("processing_error")]
    if not failures:
        session["processing_status"] = "Processed"
    elif len(failures) == len(processed_cameras):
        session["processing_status"] = "Processing failed"
    else:
        session["processing_status"] = f"Partly processed ({failures[0].get('role')} failed)"

    if save:
        session = dual_session_service.save_session(session, detections_by_role)
    return session
