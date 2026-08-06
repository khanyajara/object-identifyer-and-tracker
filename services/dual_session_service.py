"""
services/dual_session_service.py

Session metadata for dual camera recordings.

A session is the link between two independent recordings made at the same
time. Each camera keeps its own `video_id` and its own metadata record, so
the Videos page, Incidents, Analytics, and the sync API keep working exactly
as they do for single camera recordings. The session record sits on top and
says "these two videos are the same moment, seen from two angles".

    Session
      -> Front video   (video_id, detections, objects, plates)
      -> Rear video    (video_id, detections, objects, plates)
      -> Composite     (optional side-by-side processed video)

Files written:

    data/logs/sessions/ses_20260730_143522_a1b2c3.json      session record
    data/logs/recording_<...>_front.json                    per-camera record
    data/logs/recording_<...>_rear.json                     per-camera record
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional

LOGGER = logging.getLogger("roadwatch.dual_session")

SESSIONS_DIR = os.path.join("data", "logs", "sessions")
LOGS_DIR = os.path.join("data", "logs")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_dirs() -> None:
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    os.makedirs(LOGS_DIR, exist_ok=True)


def _atomic_write(path: str, payload: dict) -> None:
    _ensure_dirs()
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, default=str)
    os.replace(tmp, path)


def session_path(session_id: str) -> str:
    return os.path.join(SESSIONS_DIR, f"{session_id}.json")


def video_metadata_path(camera: dict) -> str:
    video_id = camera.get("video_id")
    if video_id:
        return os.path.join(LOGS_DIR, f"{video_id}.json")
    filename = camera.get("filename") or f"{camera.get('video_id')}.webm"
    stem = os.path.splitext(filename)[0]
    return os.path.join(LOGS_DIR, f"{stem}.json")


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------


def build_video_metadata(
    session: dict,
    camera: dict,
    detections: Optional[List[dict]] = None,
    objects_summary: Optional[Dict[str, int]] = None,
) -> dict:
    """Per-camera record in the same shape the single camera app already uses,
    plus the fields that tie it to its partner camera."""
    detections = detections or []
    role = camera.get("role")
    partners = [
        c["video_id"]
        for c in session.get("cameras", [])
        if c.get("video_id") and c.get("role") != role
    ]

    if objects_summary is None:
        objects_summary = {}
        for event in detections:
            for obj in event.get("objects", []):
                label = obj.get("label", "unknown")
                objects_summary[label] = objects_summary.get(label, 0) + 1

    return {
        "video_id": camera.get("video_id"),
        "filename": camera.get("filename"),
        "video_format": "webm",
        "video_path": camera.get("video_path"),
        "original_video_path": camera.get("video_path"),
        "processed_video_path": camera.get("processed_video_path"),
        "processing_status": camera.get("processing_status", "Pending post-processing."),
        "processing_error": camera.get("processing_error"),
        # Dual camera fields
        "session_id": session.get("session_id"),
        "camera_role": role,
        "camera_label": camera.get("label"),
        "camera_index": camera.get("camera_index"),
        "paired_video_ids": partners,
        "is_dual": len(session.get("cameras", [])) > 1,
        "composite_video_path": session.get("composite_video_path"),
        # Capture facts
        "recorded_at": session.get("started_at"),
        "duration_seconds": camera.get("duration_seconds"),
        "resolution": camera.get("resolution"),
        "codec": camera.get("codec"),
        "true_fps": camera.get("true_fps"),
        "frames_written": camera.get("frames_written"),
        "capture_status": camera.get("status"),
        "capture_error": camera.get("error"),
        "detections": detections,
        "objects_summary": objects_summary,
    }


def save_session(
    session: dict,
    detections_by_role: Optional[Dict[str, List[dict]]] = None,
) -> dict:
    """Write the session record and one metadata file per camera."""
    detections_by_role = detections_by_role or {}
    session = dict(session)
    session.setdefault("created_at", _utc_now_iso())
    session["updated_at"] = _utc_now_iso()

    written = []
    for camera in session.get("cameras", []):
        if not camera.get("video_id"):
            continue
        detections = detections_by_role.get(camera.get("role"), [])
        metadata = build_video_metadata(session, camera, detections)
        path = video_metadata_path(camera)
        _atomic_write(path, metadata)
        camera["metadata_path"] = path
        camera["detection_count"] = len(detections)
        written.append(path)

    session["metadata_files"] = written
    session["detection_counts"] = {
        role: len(events) for role, events in detections_by_role.items()
    }
    _atomic_write(session_path(session["session_id"]), session)
    LOGGER.info("Saved session %s with %s camera(s)", session["session_id"], len(written))
    return session


def update_session(session_id: str, **fields) -> Optional[dict]:
    """Patch a stored session, e.g. after post-processing finishes."""
    session = load_session(session_id)
    if session is None:
        return None
    session.update(fields)
    session["updated_at"] = _utc_now_iso()
    _atomic_write(session_path(session_id), session)
    return session


def update_camera(session_id: str, role: str, **fields) -> Optional[dict]:
    """Patch one camera inside a session and mirror it into that camera's
    video metadata file."""
    session = load_session(session_id)
    if session is None:
        return None

    for camera in session.get("cameras", []):
        if camera.get("role") != role:
            continue
        camera.update(fields)
        meta_path = camera.get("metadata_path") or video_metadata_path(camera)
        if os.path.exists(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as handle:
                    metadata = json.load(handle)
                metadata.update(
                    {
                        key: value
                        for key, value in fields.items()
                        if key
                        in (
                            "processed_video_path",
                            "processing_status",
                            "processing_error",
                            "detections",
                            "objects_summary",
                        )
                    }
                )
                _atomic_write(meta_path, metadata)
            except (OSError, json.JSONDecodeError) as exc:
                LOGGER.warning("Could not update %s: %s", meta_path, exc)

    session["updated_at"] = _utc_now_iso()
    _atomic_write(session_path(session_id), session)
    return session


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------


def load_session(session_id: str) -> Optional[dict]:
    path = session_path(session_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        LOGGER.warning("Unreadable session %s: %s", path, exc)
        return None


def list_sessions(limit: Optional[int] = None) -> List[dict]:
    """Newest first."""
    _ensure_dirs()
    sessions = []
    for name in os.listdir(SESSIONS_DIR):
        if not name.endswith(".json"):
            continue
        session = load_session(os.path.splitext(name)[0])
        if session:
            sessions.append(session)
    sessions.sort(key=lambda s: s.get("started_at") or "", reverse=True)
    return sessions[:limit] if limit else sessions


def find_session_for_video(video_id: str) -> Optional[dict]:
    """Given any camera's video_id, get the session and therefore its pair."""
    for session in list_sessions():
        if video_id in session.get("video_ids", []):
            return session
    return None


def get_partner_video(video_id: str) -> Optional[dict]:
    """The other angle of the same moment, for the Videos page toggle."""
    session = find_session_for_video(video_id)
    if not session:
        return None
    for camera in session.get("cameras", []):
        if camera.get("video_id") and camera["video_id"] != video_id:
            return camera
    return None


def session_summary_rows(limit: Optional[int] = None) -> List[dict]:
    """Flat rows for the Videos and Analytics tables."""
    rows = []
    for session in list_sessions(limit):
        cameras = session.get("cameras", [])
        rows.append(
            {
                "session_id": session.get("session_id"),
                "started_at": session.get("started_at"),
                "duration_seconds": session.get("duration_seconds"),
                "cameras": len(cameras),
                "angles": ", ".join(c.get("role", "?") for c in cameras),
                "detections": sum((session.get("detection_counts") or {}).values()),
                "composite": bool(session.get("composite_video_path")),
                "status": session.get("processing_status", "recorded"),
            }
        )
    return rows
