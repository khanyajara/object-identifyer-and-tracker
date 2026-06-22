import base64
import html
import json
import os
import threading
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from core.detector import load_yolo_model
from core.opencv_recorder import CameraManager
from core.ocr import load_ocr_reader
from core.video_io import is_playable_video_path
from services.contact_service import ContactService
from services.firebase_service import FirebaseService
from services.gps_service import GPSService
from services.incident_service import INCIDENT_TYPES, IncidentService
from services.report_service import build_summary, videos_dataframe
from services.storage_service import StorageService
from services.stolen_vehicle_service import StolenVehicleService
from services.supabase_service import SupabaseService
from services.vehicle_profile_service import VehicleProfileService
from services.video_processing_service import VideoProcessingService
from services.video_service import VideoService


PROJECT_DIR = Path(__file__).resolve().parent
SETTINGS_PATH = PROJECT_DIR / "settings.json"
os.environ.setdefault("YOLO_CONFIG_DIR", str(PROJECT_DIR / "Ultralytics"))
os.environ.setdefault("MPLCONFIGDIR", str(PROJECT_DIR / "Ultralytics"))

ENV_SETTING_KEYS = {
    "AI_API_KEY": "ai_api_key",
    "SUPABASE_URL": "supabase_url",
    "SUPABASE_ANON_KEY": "supabase_anon_key",
    "SUPABASE_BUCKET": "supabase_bucket",
    "VITE_SUPABASE_URL": "supabase_url",
    "VITE_SUPABASE_ANON_KEY": "supabase_anon_key",
    "VITE_SUPABASE_BUCKET": "supabase_bucket",
    "FIREBASE_PUSH_URL": "firebase_push_url",
    "FIREBASE_API_KEY": "firebase_api_key",
}


def load_env_file():
    env_path = PROJECT_DIR / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if value and not os.environ.get(key):
            os.environ[key] = value


load_env_file()

PERFORMANCE_MODES = {
    "turbo": {"yolo_image_size": 320, "enable_ocr": True, "enable_tracking": True},
    "balanced": {"yolo_image_size": 416, "enable_ocr": True, "enable_tracking": True},
    "high_accuracy": {"yolo_image_size": 640, "enable_ocr": True, "enable_tracking": True},
}

DEFAULTS = {
    "camera_id": "streamlit-camera-001",
    "camera_index": 0,
    "camera_width": 1280,
    "camera_height": 720,
    "camera_buffer_size": 1,
    "target_camera_fps": 30,
    "streamlit_preview_fps": 5,
    "ai_queue_maxsize": 1,
    "ai_frame_width": 640,
    "ai_frame_height": 360,
    "ai_process_interval_seconds": 0.5,
    "log_flush_interval_seconds": 5,
    "model_name": "yolov8n.pt",
    "confidence": 0.45,
    "performance_mode": "balanced",
    "yolo_image_size": 416,
    "enable_ocr": True,
    "enable_tracking": True,
    "enable_movement": True,
    "ocr_interval_seconds": 5,
    "save_snapshots": False,
    "save_annotated_video": True,
    "sync_url": "",
    "sync_api_key": "",
    "ai_api_key": "",
    "supabase_url": "",
    "supabase_anon_key": "",
    "supabase_bucket": "videos",
    "firebase_push_url": "",
    "firebase_api_key": "",
}


def load_settings():
    env_settings = {
        setting_key: os.environ[env_key]
        for env_key, setting_key in ENV_SETTING_KEYS.items()
        if os.environ.get(env_key)
    }
    if SETTINGS_PATH.exists():
        try:
            return {**DEFAULTS, **json.loads(SETTINGS_PATH.read_text("utf-8")), **env_settings}
        except (OSError, json.JSONDecodeError):
            pass
    return {**DEFAULTS, **env_settings}


def save_settings(settings):
    SETTINGS_PATH.write_text(json.dumps(settings, indent=2), encoding="utf-8")


@st.cache_resource(show_spinner="Loading YOLO model...")
def cached_model(name):
    return load_yolo_model(name)


@st.cache_resource(show_spinner="Loading OCR...")
def cached_ocr():
    return load_ocr_reader()


def styles():
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Rajdhani:wght@500;600;700&display=swap');
        :root{
            --rw-bg:#02070d;
            --rw-panel:rgba(3,18,28,.72);
            --rw-panel-2:rgba(5,28,42,.62);
            --rw-line:rgba(34,211,238,.24);
            --rw-line-hot:rgba(34,211,238,.78);
            --rw-cyan:#22d3ee;
            --rw-cyan-2:#67e8f9;
            --rw-green:#22c55e;
            --rw-red:#ff4545;
            --rw-yellow:#facc15;
            --rw-muted:#8ea7b8;
        }
        html,body,.stApp{
            background:
                radial-gradient(circle at 18% 8%,rgba(34,211,238,.12),transparent 28%),
                radial-gradient(circle at 85% 0%,rgba(239,68,68,.10),transparent 22%),
                linear-gradient(135deg,#010409,#03111b 42%,#06131d 100%) !important;
            color:#eafcff;
            font-family:'Rajdhani','Inter',system-ui,sans-serif;
        }
        .stApp::before{
            content:"";
            position:fixed;inset:0;pointer-events:none;z-index:0;
            background-image:
                linear-gradient(rgba(34,211,238,.035) 1px,transparent 1px),
                linear-gradient(90deg,rgba(34,211,238,.035) 1px,transparent 1px);
            background-size:42px 42px;
            mask-image:linear-gradient(to bottom,rgba(0,0,0,.75),transparent 85%);
        }
        [data-testid="stSidebar"]{
            background:linear-gradient(180deg,rgba(2,10,17,.98),rgba(3,19,28,.96)) !important;
            border-right:1px solid rgba(34,211,238,.22);
            box-shadow:18px 0 44px rgba(0,0,0,.42), inset -1px 0 18px rgba(34,211,238,.08);
        }
        [data-testid="stSidebar"]::before{
            content:"";
            position:absolute;inset:0;
            background:linear-gradient(90deg,rgba(34,211,238,.04),transparent 58%);
            pointer-events:none;
        }
        [data-testid="stSidebar"] .stRadio label{
            color:#d9f9ff !important;
            letter-spacing:.08em;
            text-transform:uppercase;
            font-size:.86rem;
        }
        [data-testid="stSidebar"] [role="radiogroup"] label{
            border:1px solid transparent;
            border-radius:12px;
            padding:.32rem .4rem;
            margin:.22rem 0;
        }
        [data-testid="stSidebar"] [role="radiogroup"] label:hover{
            border-color:rgba(34,211,238,.35);
            background:rgba(34,211,238,.08);
        }
        .block-container{
            position:relative;z-index:1;
            max-width:1640px;
            padding-top:.8rem;
            padding-bottom:2rem;
        }
        .rw-sidebar-brand{
            border:1px solid rgba(34,211,238,.28);
            border-radius:18px;
            padding:1rem;
            background:linear-gradient(145deg,rgba(5,30,44,.82),rgba(2,9,15,.82));
            box-shadow:0 0 28px rgba(34,211,238,.08), inset 0 0 22px rgba(34,211,238,.04);
            margin-bottom:1rem;
        }
        .rw-brand-title{font-size:1.35rem;font-weight:900;letter-spacing:.12em;color:var(--rw-cyan)}
        .rw-brand-sub{font-size:.72rem;color:#7dd3fc;letter-spacing:.2em;text-transform:uppercase}
        .rw-side-status{margin-top:1rem;border:1px solid rgba(34,211,238,.18);border-radius:16px;background:rgba(3,16,25,.72);padding:.85rem}
        .rw-side-row{display:flex;justify-content:space-between;border-bottom:1px solid rgba(148,163,184,.12);padding:.48rem 0;font-size:.78rem;letter-spacing:.06em;text-transform:uppercase}
        .rw-side-row:last-child{border-bottom:0}
        .rw-online{color:#22c55e;font-weight:900}
        .system-bar{
            position:sticky;top:.35rem;z-index:50;
            display:grid;grid-template-columns:repeat(7,minmax(110px,1fr));
            gap:.65rem;align-items:stretch;
            border:1px solid rgba(34,211,238,.20);
            border-radius:18px;
            padding:.72rem .9rem;
            margin-bottom:.75rem;
            background:linear-gradient(180deg,rgba(2,12,20,.93),rgba(2,8,14,.86));
            box-shadow:0 14px 42px rgba(0,0,0,.35), inset 0 1px 0 rgba(255,255,255,.04);
            backdrop-filter:blur(18px);
        }
        .system-cell{border-right:1px solid rgba(34,211,238,.12);padding:.2rem .7rem}
        .system-cell:last-child{border-right:0}
        .system-label{color:#a8c7d7;font-size:.68rem;letter-spacing:.14em;text-transform:uppercase;font-weight:700}
        .system-value{color:#f4feff;font-size:1rem;font-weight:900;letter-spacing:.08em}
        .system-value.cyan{color:var(--rw-cyan)}
        .system-value.red{color:#ff6b6b;text-shadow:0 0 18px rgba(239,68,68,.45)}
        .eyebrow{color:var(--rw-cyan);font-weight:900;letter-spacing:.22em;text-transform:uppercase;font-size:.75rem}
        .hero{font-size:clamp(2rem,4vw,3.9rem);font-weight:900;letter-spacing:-.045em;margin:.05rem 0;color:#effcff;text-shadow:0 0 35px rgba(34,211,238,.12)}
        .muted{color:var(--rw-muted)}
        .panel,.video-card,.metric-card{
            position:relative;
            background:linear-gradient(180deg,var(--rw-panel),rgba(1,9,15,.72));
            border:1px solid var(--rw-line);
            border-radius:18px;
            box-shadow:0 18px 42px rgba(0,0,0,.35), inset 0 1px 0 rgba(255,255,255,.035);
            backdrop-filter:blur(16px);
        }
        .panel::before,.video-card::before,.metric-card::before{
            content:"";position:absolute;left:14px;right:14px;top:0;height:1px;
            background:linear-gradient(90deg,transparent,var(--rw-cyan),transparent);
            opacity:.55;
        }
        .panel{padding:1rem}
        .hud-panel{padding:.85rem;border:1px solid rgba(34,211,238,.26);border-radius:18px;background:rgba(1,12,19,.74)}
        .panel-title{font-weight:900;letter-spacing:.13em;text-transform:uppercase;color:#b8f7ff;font-size:.9rem;margin-bottom:.8rem}
        .metric-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));gap:.75rem}
        .metric-card{padding:.95rem;overflow:hidden}
        .metric-label{color:#9bd8e5;font-size:.68rem;font-weight:900;letter-spacing:.14em;text-transform:uppercase}
        .metric-value{font-size:1.7rem;font-weight:900;color:#ecfeff;line-height:1.05}
        .metric-spark{height:30px;margin-top:.5rem;background:linear-gradient(90deg,transparent,rgba(34,211,238,.26),transparent);clip-path:polygon(0 70%,8% 45%,15% 60%,23% 30%,31% 76%,40% 52%,48% 78%,56% 42%,64% 60%,72% 25%,82% 68%,92% 44%,100% 72%)}
        .badge{display:inline-flex;align-items:center;gap:.35rem;border-radius:999px;padding:.35rem .7rem;font-size:.78rem;font-weight:900;border:1px solid rgba(34,211,238,.28);letter-spacing:.08em;text-transform:uppercase}
        .badge-green{background:rgba(34,197,94,.12);color:#86efac}
        .badge-red{background:rgba(239,68,68,.14);color:#fca5a5;border-color:rgba(239,68,68,.4)}
        .badge-yellow{background:rgba(245,158,11,.13);color:#fcd34d;border-color:rgba(245,158,11,.34)}
        .badge-blue{background:rgba(34,211,238,.11);color:#67e8f9}
        .video-card{padding:1rem;margin:.75rem 0}
        .warning-panel{border:1px solid rgba(251,191,36,.35);background:rgba(251,191,36,.08);border-radius:16px;padding:1rem}
        .camera-frame{
            position:relative;overflow:hidden;
            min-height:640px;
            display:grid;place-items:center;text-align:center;
            border:1px solid rgba(34,211,238,.38);
            border-radius:22px;
            background:
                linear-gradient(rgba(34,211,238,.06),rgba(34,211,238,.02)),
                radial-gradient(circle at center,rgba(34,211,238,.10),transparent 55%),
                #020812;
            box-shadow:0 0 0 1px rgba(34,211,238,.08),0 22px 70px rgba(0,0,0,.48), inset 0 0 55px rgba(34,211,238,.08);
        }
        .camera-frame::before{
            content:"";position:absolute;inset:18px;border:1px solid rgba(34,211,238,.24);border-radius:16px;
            background:
                linear-gradient(90deg,rgba(34,211,238,.35) 0 38px,transparent 38px calc(100% - 38px),rgba(34,211,238,.35) calc(100% - 38px)),
                linear-gradient(rgba(34,211,238,.35) 0 38px,transparent 38px calc(100% - 38px),rgba(34,211,238,.35) calc(100% - 38px));
            pointer-events:none;
        }
        .camera-frame::after{
            content:"LIVE HUD";position:absolute;right:32px;top:30px;color:var(--rw-cyan);font-weight:900;letter-spacing:.18em;font-size:.8rem;
        }
        .hud-readout{position:absolute;left:34px;bottom:28px;text-align:left;color:var(--rw-cyan)}
        .hud-speed{font-size:3rem;font-weight:900;line-height:.9;color:#dffcff;text-shadow:0 0 25px rgba(34,211,238,.28)}
        .hud-coords{position:absolute;bottom:30px;left:34%;right:34%;color:var(--rw-cyan);font-weight:900;letter-spacing:.09em}
        .hud-time{position:absolute;right:34px;bottom:28px;text-align:right;color:#dffcff;font-size:1.1rem}
        [data-testid="stImage"] img{
            width:100% !important;
            max-height:760px !important;
            object-fit:contain !important;
            background:#020617;
            border-radius:22px;
            border:1px solid rgba(34,211,238,.36);
            box-shadow:0 20px 70px rgba(0,0,0,.48),0 0 35px rgba(34,211,238,.08);
        }
        video{border-radius:18px;background:#020617;border:1px solid rgba(34,211,238,.26)}
        .timeline{height:56px;border:1px solid rgba(34,211,238,.18);border-radius:16px;padding:1rem;background:rgba(2,15,23,.68)}
        .timeline-track{height:14px;border-radius:999px;background:linear-gradient(90deg,#009a9a 0 18%,#d6a400 18% 24%,#008e91 24% 45%,#0ea5e9 45% 58%,#ef4444 58% 60%,#008e91 60% 100%);box-shadow:0 0 18px rgba(34,211,238,.16)}
        .list-row{display:flex;justify-content:space-between;gap:1rem;border-bottom:1px solid rgba(148,163,184,.12);padding:.72rem 0}
        .list-row:last-child{border-bottom:0}
        .evidence-thumb{height:96px;border-radius:14px;border:1px solid rgba(34,211,238,.22);background:radial-gradient(circle at 50% 20%,rgba(34,211,238,.18),transparent 42%),linear-gradient(135deg,#020617,#082f49);display:grid;place-items:center;color:#67e8f9;font-weight:900;letter-spacing:.18em}
        .archive-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:.85rem;margin:.8rem 0 1rem}
        .archive-card{border:1px solid rgba(34,211,238,.22);border-radius:18px;background:rgba(3,18,28,.72);padding:1rem}
        .danger{color:#ff6b6b}.warn{color:#facc15}.good{color:#22c55e}.cyan{color:var(--rw-cyan)}
        div[data-testid="stButton"] button{
            border:1px solid rgba(34,211,238,.34);
            border-radius:12px;
            background:linear-gradient(180deg,rgba(8,47,73,.72),rgba(2,18,28,.85));
            color:#dffcff;
            font-weight:900;
            letter-spacing:.08em;
            text-transform:uppercase;
            box-shadow:inset 0 1px 0 rgba(255,255,255,.06),0 0 18px rgba(34,211,238,.08);
        }
        div[data-testid="stButton"] button:hover{border-color:var(--rw-cyan);box-shadow:0 0 24px rgba(34,211,238,.18)}
        .stDataFrame,.stTable{border:1px solid rgba(34,211,238,.16);border-radius:16px;overflow:hidden}
        </style>
        """,
        unsafe_allow_html=True,
    )


def header(eyebrow, title, copy):
    st.markdown(
        f'<div class="eyebrow">{eyebrow}</div><div class="hero">{title}</div>'
        f'<p class="muted">{copy}</p>',
        unsafe_allow_html=True,
    )


def badge(text, tone="blue"):
    st.markdown(
        f'<span class="badge badge-{tone}">{text}</span>',
        unsafe_allow_html=True,
    )


def esc(value):
    return html.escape(str(value or ""))


def system_top_bar(settings):
    camera_manager = st.session_state.get("camera_manager")
    recording = bool(camera_manager and camera_manager.active)
    elapsed = format_duration(camera_manager.elapsed) if recording else "00:00"
    now = datetime.now().strftime("%H:%M:%S")
    mode = "ACTIVE" if recording else "STANDBY"
    st.markdown(
        f"""
        <div class="system-bar">
          <div class="system-cell"><div class="system-label">System mode</div><div class="system-value cyan">{mode}</div></div>
          <div class="system-cell"><div class="system-label">Recorder</div><div class="system-value {'red' if recording else 'cyan'}">{'RECORDING' if recording else 'READY'}</div></div>
          <div class="system-cell"><div class="system-label">Session</div><div class="system-value">{elapsed}</div></div>
          <div class="system-cell"><div class="system-label">GPS</div><div class="system-value cyan">LOCKED</div></div>
          <div class="system-cell"><div class="system-label">Network</div><div class="system-value cyan">5G</div></div>
          <div class="system-cell"><div class="system-label">Cameras</div><div class="system-value cyan">FRONT + REAR</div></div>
          <div class="system-cell"><div class="system-label">Clock</div><div class="system-value">{now}</div></div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def sidebar_brand(active_page):
    st.sidebar.markdown(
        f"""
        <div class="rw-sidebar-brand">
          <div class="rw-brand-title">ROADWATCH</div>
          <div class="rw-brand-sub">Vision Recorder</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def sidebar_status(settings):
    camera_manager = st.session_state.get("camera_manager")
    recorder = "ACTIVE" if camera_manager and camera_manager.active else "READY"
    st.sidebar.markdown(
        f"""
        <div class="rw-side-status">
          <div class="panel-title" style="margin-bottom:.45rem">System Status</div>
          <div class="rw-side-row"><span>AI Scanner</span><span class="rw-online">ONLINE</span></div>
          <div class="rw-side-row"><span>Storage</span><span class="rw-online">READY</span></div>
          <div class="rw-side-row"><span>Sync Service</span><span class="rw-online">ONLINE</span></div>
          <div class="rw-side-row"><span>Camera</span><span class="rw-online">{recorder}</span></div>
          <div class="rw-side-row"><span>Resolution</span><span>{settings["camera_width"]}x{settings["camera_height"]}</span></div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def cards_html(items):
    cards = "".join(
        f'<div class="metric-card"><div class="metric-label">{label}</div>'
        f'<div class="metric-value">{value}</div>'
        f'<div class="muted" style="font-size:.78rem">{hint}</div></div>'
        for label, value, hint in items
    )
    st.markdown(f'<div class="metric-grid">{cards}</div>', unsafe_allow_html=True)


def format_duration(seconds):
    seconds = int(seconds or 0)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def latest_event_from_record(record):
    detections = record.get("detections", [])
    return detections[-1] if detections else {}


def event_object_labels(event):
    return sorted(
        {
            item.get("label")
            for item in event.get("objects", [])
            if item.get("label")
        }
    )


def snapshot_data_url(snapshot_path):
    if not snapshot_path:
        return None
    path = Path(snapshot_path)
    if not path.exists() or not path.is_file():
        return None
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return (
        f"data:{mime};base64,"
        + base64.b64encode(path.read_bytes()).decode("ascii")
    )


def unique_tracking_ids(record):
    stored = record.get("objects_summary", {}).get("unique_tracking_ids")
    if stored is not None:
        return sorted(stored)
    return sorted(
        {
            item.get("tracking_id")
            for detection in record.get("detections", [])
            for item in detection.get("objects", [])
            if item.get("tracking_id") is not None
        }
    )


def captured_objects_for_video(record):
    captured = []
    for detection in record.get("detections", []):
        detection_video_id = detection.get("video_id")
        if (
            detection_video_id is not None
            and detection_video_id != record.get("video_id")
        ):
            continue
        snapshots = detection.get("snapshots", [])
        snapshot = snapshot_data_url(snapshots[0]) if snapshots else None
        for item in detection.get("objects", []):
            object_video_id = item.get("video_id", detection.get("video_id"))
            if object_video_id != record.get("video_id"):
                continue
            box = item.get("box", {})
            captured.append(
                {
                    "Video ID": object_video_id,
                    "Time": detection.get("timestamp"),
                    "Frame number": detection.get("frame_number"),
                    "Object label": item.get("label"),
                    "Tracking ID": item.get("tracking_id"),
                    "Confidence": round(float(item.get("confidence") or 0), 1),
                    "Bounding box": (
                        f'x={box.get("x")}, y={box.get("y")}, '
                        f'w={box.get("width")}, h={box.get("height")}'
                    ),
                    "Plate text": detection.get("plate_text"),
                    "Movement detected": detection.get("movement_detected", False),
                    "Snapshot preview": snapshot,
                }
            )
    return captured


def video_path_candidates(record, version):
    candidates = []
    if version == "Processed":
        processed = record.get("processed_video_path")
        if processed:
            candidates.append(Path(processed))
        candidates.append(
            PROJECT_DIR
            / "data"
            / "videos"
            / "processed"
            / f'recording_{record["video_id"]}_processed.mp4'
        )
    for key in ("original_video_path", "video_path"):
        value = record.get(key)
        if value:
            candidates.append(Path(value))
            candidates.append(PROJECT_DIR / "data" / "videos" / Path(value).name)
    unique = []
    seen = set()
    for path in candidates:
        path = path if path.is_absolute() else PROJECT_DIR / path
        key = str(path)
        if key not in seen:
            unique.append(path)
            seen.add(key)
    return unique


def first_playable_video_path(record, version):
    for path in video_path_candidates(record, version):
        if is_playable_video_path(path):
            return path
    return None


def render_video_player(record):
    processed_path = first_playable_video_path(record, "Processed")
    original_path = first_playable_video_path(record, "Original")
    versions = []
    if processed_path:
        versions.append("Processed")
    if original_path:
        versions.append("Original")
    if not versions:
        st.warning(
            "Video file unavailable. The metadata exists, but the MP4 is "
            "missing or unreadable on disk."
        )
        with st.expander("Checked video paths", expanded=False):
            checked = [
                {"Version": version, "Path": str(path)}
                for version in ("Processed", "Original")
                for path in video_path_candidates(record, version)
            ]
            st.dataframe(pd.DataFrame(checked), width="stretch")
        return None
    version_key = f'video-version-{record["video_id"]}'
    if st.session_state.get(version_key) not in versions:
        st.session_state[version_key] = versions[0]
    version = st.radio(
        "Video version",
        versions,
        horizontal=True,
        key=version_key,
    )
    path = processed_path if version == "Processed" else original_path
    try:
        st.video(path.read_bytes(), format="video/mp4")
        st.caption(str(path))
        return path
    except OSError as exc:
        st.warning(f"Could not read video file: {exc}")
        return None


def get_camera_state(settings):
    if "camera_manager" not in st.session_state:
        st.session_state.camera_manager = None
    camera_manager = st.session_state.camera_manager
    active = bool(camera_manager and camera_manager.active)
    return camera_manager, active


def stop_and_process(camera_manager, settings):
    processing_status = st.status("Processing detection overlays...", expanded=True)
    record = camera_manager.stop()
    st.session_state.camera_manager = None
    record = VideoProcessingService(
        settings,
        cached_model(settings["model_name"]),
        cached_ocr() if settings["enable_ocr"] else None,
    ).process(record)
    IncidentService().sync_generated(
        [record], StolenVehicleService().list_reports()
    )
    st.session_state.last_record = record
    if record.get("processed_video_path"):
        processing_status.update(
            label="Processed video saved successfully.",
            state="complete",
            expanded=False,
        )
    else:
        processing_status.update(
            label="Post-processing failed; original video preserved.",
            state="error",
            expanded=True,
        )
        st.error(record.get("processing_error"))
    return record


def dash_cam_page(settings):
    camera_manager, active = get_camera_state(settings)
    gps = GPSService().status()
    storage_rows = StorageService().status()
    videos = VideoService().list_videos()
    latest_videos = videos[:3]
    latest_record = latest_videos[0] if latest_videos else {}
    latest_event = latest_event_from_record(latest_record) if latest_record else {}
    latest_summary = latest_record.get("objects_summary", {}) if latest_record else {}

    st.markdown(
        """
        <div class="eyebrow">Live dash cam</div>
        <div class="panel-title" style="font-size:1.05rem;margin-bottom:.75rem">Security fleet monitoring console</div>
        """,
        unsafe_allow_html=True,
    )

    controls = st.columns([1.2, 1.2, 1.2, 4])
    with controls[0]:
        if st.button("Start Recording", type="primary", disabled=active, width="stretch"):
            try:
                camera_manager = CameraManager(
                    settings,
                    cached_model(settings["model_name"]),
                    cached_ocr() if settings["enable_ocr"] else None,
                )
                st.session_state.camera_manager = camera_manager
                st.session_state.last_record = None
                active = True
            except Exception as exc:
                st.error(str(exc))
    with controls[1]:
        if st.button("Stop Recording", disabled=not active, width="stretch"):
            if camera_manager:
                stop_and_process(camera_manager, settings)
            active = False
    with controls[2]:
        if st.button("SOS", width="stretch"):
            st.warning(
                f"SOS UI triggered. {ContactService().active_count()} active contacts would be notified when a notification service is configured."
            )
    with controls[3]:
        if active:
            badge("Recording active", "red")
        else:
            badge("Standby", "blue")

    main_col, side_col = st.columns([2.15, 1])
    with main_col:
        st.markdown('<div class="panel-title">Live Dash Cam</div>', unsafe_allow_html=True)
        frame_placeholder = st.empty()
        if not active:
            frame_placeholder.markdown(
                f"""
                <div class="camera-frame">
                  <div>
                    <div style="font-size:2.25rem;font-weight:900;letter-spacing:.08em;color:#eaffff">CAMERA READY</div>
                    <p class="muted">Press Start Recording to open the OpenCV camera and activate Roadwatch AI overlays.</p>
                  </div>
                  <div class="hud-readout"><div class="metric-label">Speed</div><div class="hud-speed">{gps["speed_kmh"]}</div><div>KM/H</div></div>
                  <div class="hud-coords">{gps["latitude"] or "25.2048 S"} , {gps["longitude"] or "28.0473 E"}</div>
                  <div class="hud-time">{datetime.now().strftime("%H:%M:%S")}<br>{datetime.now().strftime("%d/%m/%Y")}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
    with side_col:
        st.markdown(
            """
            <div class="panel">
              <div class="panel-title">Secondary Camera (Rear)</div>
              <div class="camera-frame" style="min-height:190px;border-radius:16px">
                <div>
                  <div class="metric-label">Rear Feed</div>
                  <div style="font-size:1.25rem;font-weight:900;color:#eaffff">STANDBY MIRROR</div>
                  <p class="muted">Use this slot for rear-camera stream when configured.</p>
                </div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        telemetry_box = st.empty()
        gps_box = st.empty()

    timeline_box = st.empty()
    bottom_metrics_box = st.empty()
    lower_a, lower_b, lower_c, lower_d = st.columns([1.15, 1.15, 1.15, 1])
    with lower_a:
        incidents_box = st.empty()
    with lower_b:
        detections_box = st.empty()
    with lower_c:
        notifications_box = st.empty()
    with lower_d:
        quick_actions_box = st.empty()

    def render_static_hud(event=None, metrics=None, error=None, elapsed="00:00"):
        event = event or {}
        metrics = metrics or {
            "camera_fps": 0,
            "ai_fps": 0,
            "processing_time_ms": 0,
            "active_resolution": f'{settings["camera_width"]}x{settings["camera_height"]}',
        }
        objects = event.get("objects", [])
        labels = event_object_labels(event) if event else []
        label_text = ", ".join(labels[:4]) if labels else "Awaiting scan"
        movement = "DETECTED" if event.get("movement_detected") else "CLEAR"
        plate = event.get("plate_text") or (latest_summary.get("plates_detected", ["None"]) or ["None"])[0]
        people = event.get("people_count", latest_summary.get("people_count_max", 0))
        vehicles = event.get("vehicle_count", latest_summary.get("vehicle_count_max", 0))
        storage_ready = sum(1 for row in storage_rows if row["Exists"])
        ai_status = "ERROR" if error else ("ONLINE" if active else "STANDBY")
        telemetry_box.markdown(
            f"""
            <div class="metric-grid" style="grid-template-columns:1fr 1fr">
              <div class="metric-card"><div class="metric-label">AI Telemetry</div><div class="list-row"><span>Model</span><span>YOLOv8</span></div><div class="list-row"><span>Tracking</span><span>{'Deep SORT' if settings.get("enable_tracking") else 'Sampled'}</span></div><div class="list-row"><span>OCR</span><span>{'Active' if settings.get("enable_ocr") else 'Off'}</span></div><div class="list-row"><span>Frame Rate</span><span>{metrics.get("camera_fps", 0):.1f} FPS</span></div><div class="list-row"><span>Processing</span><span>{metrics.get("processing_time_ms", 0):.0f} ms</span></div></div>
              <div class="metric-card"><div class="metric-label">GPS Status</div><div class="metric-value" style="font-size:1rem;color:#22c55e">{gps["status"]}</div><div style="margin-top:.8rem;color:#dffcff">{gps["latitude"] or "25.2048 S"}<br>{gps["longitude"] or "28.0473 E"}<br><span class="muted">Altitude: 1398 m</span></div><div class="metric-spark"></div></div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        gps_box.markdown(
            f"""
            <div class="metric-grid" style="grid-template-columns:1fr 1fr">
              <div class="metric-card"><div class="metric-label">Object Summary</div><div class="list-row"><span>Vehicles</span><span>{vehicles}</span></div><div class="list-row"><span>People</span><span>{people}</span></div><div class="list-row"><span>Other</span><span>{max(len(objects) - people - vehicles, 0)}</span></div></div>
              <div class="metric-card"><div class="metric-label">Today's Stats</div><div class="list-row"><span>Recordings</span><span>{len(videos)}</span></div><div class="list-row"><span>Incidents</span><span>{latest_summary.get("movement_events", 0)}</span></div><div class="list-row"><span>Distance</span><span>148 km</span></div><div class="list-row"><span>Drive Time</span><span>{elapsed}</span></div></div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        bottom_metrics_box.markdown(
            f"""
            <div class="metric-grid" style="margin-top:.85rem">
              <div class="metric-card"><div class="metric-label">AI Scanner</div><div class="metric-value" style="font-size:1.05rem;color:#22c55e">{ai_status}</div><div class="muted">Processing frames</div><div class="metric-spark"></div></div>
              <div class="metric-card"><div class="metric-label">Objects Detected</div><div class="metric-value">{len(objects) or sum(latest_summary.get("object_counts", {}).values())}</div><div class="muted">Vehicles {vehicles} / People {people}</div></div>
              <div class="metric-card"><div class="metric-label">Movement</div><div class="metric-value" style="font-size:1rem;color:{'#22c55e' if movement == 'CLEAR' else '#facc15'}">{movement}</div><div class="metric-spark"></div></div>
              <div class="metric-card"><div class="metric-label">Latest Plate</div><div class="metric-value" style="font-size:1.25rem">{plate}</div><div class="muted">Confidence 92%</div></div>
              <div class="metric-card"><div class="metric-label">Storage</div><div class="metric-value">{int((storage_ready / max(len(storage_rows), 1)) * 100)}%</div><div class="muted">{storage_ready}/{len(storage_rows)} folders ready</div><div class="metric-spark"></div></div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        timeline_box.markdown(
            """
            <div class="timeline" style="margin-top:.85rem">
              <div style="display:flex;justify-content:space-between;margin-bottom:.55rem"><span class="panel-title" style="margin:0">Session Timeline</span><span class="muted">Normal / Motion / Incident</span></div>
              <div class="timeline-track"></div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        incidents_box.markdown(
            """
            <div class="panel">
              <div class="panel-title">Recent Incidents</div>
              <div class="list-row"><span class="danger">Possible stolen vehicle</span><span>HIGH</span></div>
              <div class="list-row"><span class="warn">Hard braking detected</span><span>MEDIUM</span></div>
              <div class="list-row"><span class="cyan">Person detected</span><span>LOW</span></div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        detections_box.markdown(
            f"""
            <div class="panel">
              <div class="panel-title">AI Detections (Live)</div>
              <div class="list-row"><span>{label_text}</span><span>{datetime.now().strftime("%H:%M:%S")}</span></div>
              <div class="list-row"><span>ID scan</span><span>{len(objects)} objects</span></div>
              <div class="list-row"><span>AI FPS</span><span>{metrics.get("ai_fps", 0):.1f}</span></div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        notifications_box.markdown(
            f"""
            <div class="panel">
              <div class="panel-title">System Notifications</div>
              <div class="list-row"><span class="good">Video storage ready</span><span>{datetime.now().strftime("%H:%M:%S")}</span></div>
              <div class="list-row"><span class="warn">High memory watch</span><span>OK</span></div>
              <div class="list-row"><span class="cyan">Sync bucket</span><span>{settings.get("supabase_bucket", "Local")}</span></div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        quick_actions_box.markdown(
            """
            <div class="panel">
              <div class="panel-title">Quick Actions</div>
              <div class="list-row"><span>Take snapshot</span><span>Ready</span></div>
              <div class="list-row"><span>Manual incident</span><span>Ready</span></div>
              <div class="list-row"><span>Lock current clip</span><span>Ready</span></div>
              <div class="list-row"><span>Voice note</span><span>Ready</span></div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    if active:
        @st.fragment(run_every=1 / settings["streamlit_preview_fps"])
        def dash_fragment():
            current = st.session_state.get("camera_manager")
            if not current or not current.active:
                return
            frame, event, metrics, error = current.get_dashboard_state()
            if frame is not None:
                frame_placeholder.image(frame, channels="BGR", width="stretch")
            elapsed = format_duration(current.elapsed)
            render_static_hud(event, metrics, error, elapsed)
            if error:
                st.warning(f"AI scanner offline: {error}")

        dash_fragment()
    else:
        render_static_hud(latest_event, None, None, "00:00")
    if st.session_state.get("last_record"):
        record = st.session_state.last_record
        st.success(f'Saved {record["filename"]}. {record.get("processing_status", "")}')


def video_matches_query(video, query):
    if not query:
        return True
    query = query.lower()
    haystack = [
        video.get("filename", ""),
        video.get("started_at", ""),
        " ".join(video.get("objects_summary", {}).get("plates_detected", [])),
    ]
    for detection in video.get("detections", []):
        haystack.append(detection.get("plate_text") or "")
        haystack.extend(event_object_labels(detection))
    return query in " ".join(haystack).lower()


def video_matches_filter(video, selected_filter):
    summary = video.get("objects_summary", {})
    if selected_filter == "All videos":
        return True
    if selected_filter == "Processed videos":
        return bool(video.get("processed_video_path"))
    if selected_filter == "Original only":
        return not video.get("processed_video_path")
    if selected_filter == "With plates":
        return bool(summary.get("plates_detected"))
    if selected_filter == "With movement":
        return summary.get("movement_events", 0) > 0
    if selected_filter == "With people":
        return summary.get("people_count_max", 0) > 0
    if selected_filter == "With vehicles":
        return summary.get("vehicle_count_max", 0) > 0
    if selected_filter == "With possible incidents":
        return bool(video.get("processing_error")) or summary.get("movement_events", 0) > 0
    return True


def upload_processed_video_background(video_id, settings):
    service = VideoService()
    try:
        service.update_sync_fields(video_id, sync_status="Uploading processed video", sync_error=None)
        record = service.load(video_id)
        upload = SupabaseService(
            settings.get("supabase_url", ""),
            settings.get("supabase_anon_key", ""),
            settings.get("supabase_bucket", "videos"),
        ).upload_processed_video(record)

        firebase_result = FirebaseService(
            settings.get("firebase_push_url", ""),
            settings.get("firebase_api_key", ""),
        ).push_video_link(record, upload["public_url"])

        service.update_sync_fields(
            video_id,
            sync_status="Synced",
            synced_at=datetime.now().isoformat(),
            supabase_bucket=upload["bucket"],
            supabase_object_name=upload["object_name"],
            supabase_processed_url=upload["public_url"],
            firebase_push_url=settings.get("firebase_push_url", ""),
            firebase_push_result=firebase_result,
            sync_error=None,
        )
    except Exception as exc:
        service.update_sync_fields(
            video_id,
            sync_status="Sync failed",
            sync_error=str(exc),
        )


def queue_processed_video_upload(record, settings):
    processed_path = record.get("processed_video_path")
    if not processed_path:
        raise RuntimeError("Only processed videos can be uploaded. Generate the processed video first.")
    if not Path(processed_path).exists():
        raise RuntimeError(f"Processed video file not found: {processed_path}")
    if not settings.get("supabase_url") or not settings.get("supabase_anon_key"):
        raise RuntimeError("Configure Supabase URL and anon key before syncing.")

    VideoService().update_sync_fields(record["video_id"], sync_status="Upload queued", sync_error=None)
    worker = threading.Thread(
        target=upload_processed_video_background,
        args=(record["video_id"], dict(settings)),
        daemon=True,
    )
    worker.start()


def videos_page(service, settings):
    header(
        "Evidence library",
        "Videos",
        "Search recordings, inspect processed evidence, and review captured objects.",
    )
    videos = service.list_videos()
    if not videos:
        st.info("No recordings yet.")
        return
    search, filter_col = st.columns([2, 1])
    query = search.text_input("Search by filename, object, plate, or date")
    selected_filter = filter_col.selectbox(
        "Filter",
        [
            "All videos",
            "Processed videos",
            "Original only",
            "With plates",
            "With movement",
            "With people",
            "With vehicles",
            "With possible incidents",
        ],
    )
    filtered = [
        video for video in videos
        if video_matches_query(video, query)
        and video_matches_filter(video, selected_filter)
    ]
    if filtered:
        st.markdown('<div class="panel-title">Evidence Library Video Cards</div>', unsafe_allow_html=True)
        for row_start in range(0, min(len(filtered), 4), 2):
            cols = st.columns(2)
            for col, video in zip(cols, filtered[row_start:row_start + 2]):
                summary = video.get("objects_summary", {})
                plates = ", ".join(summary.get("plates_detected", [])) or "No plates"
                with col:
                    with st.container(border=True):
                        st.markdown('<div class="evidence-thumb">VIDEO</div>', unsafe_allow_html=True)
                        st.markdown(f'#### {video.get("filename", "Recording")}')
                        st.caption(video.get("processing_status", video.get("sync_status", "Local evidence")))
                        c1, c2 = st.columns(2)
                        c1.metric("Duration", format_duration(video.get("duration_seconds", 0)))
                        c2.metric("Objects", sum(summary.get("object_counts", {}).values()))
                        st.write(f'**Started:** {video.get("started_at", "")[:19]}')
                        st.write(f"**Plates:** {plates}")
                        if video.get("supabase_processed_url"):
                            st.link_button("Open uploaded clip", video["supabase_processed_url"], width="stretch")
    st.dataframe(videos_dataframe(filtered), width="stretch", hide_index=True)
    if not filtered:
        st.warning("No videos match that search/filter.")
        return
    labels = {
        f'{video["filename"]} | {video.get("started_at", "")[:16]}': video["video_id"]
        for video in filtered
    }
    selected_video_id = labels[st.selectbox("Open recording", list(labels))]
    selected = service.load(selected_video_id)
    summary = selected.get("objects_summary", {})
    tracking_ids = unique_tracking_ids(selected)
    st.markdown('<div class="video-card">', unsafe_allow_html=True)
    top = st.columns([2, 1])
    with top[0]:
        render_video_player(selected)
        if selected.get("processing_error"):
            st.warning("Processed video unavailable: " + selected["processing_error"])
    with top[1]:
        cards_html(
            [
                ("Duration", format_duration(selected.get("duration_seconds", 0)), "Recording length"),
                ("Status", selected.get("processing_status", "Not processed"), "Processing"),
                ("Objects", sum(summary.get("object_counts", {}).values()), "Peak/tracked count"),
                ("Plates", len(summary.get("plates_detected", [])), "Unique OCR results"),
                ("Movement", summary.get("movement_events", 0), "Events"),
                ("Tracked IDs", len(tracking_ids), "Unique objects"),
            ]
        )
        if selected.get("supabase_processed_url"):
            st.link_button("Open Supabase processed video", selected["supabase_processed_url"], width="stretch")
        if selected.get("firebase_push_url"):
            st.caption(f'Firebase link target: {selected["firebase_push_url"]}')
        if selected.get("sync_error"):
            st.error(selected["sync_error"])
        if st.button("Upload processed video", width="stretch"):
            try:
                queue_processed_video_upload(selected, settings)
                st.success("Processed video upload queued. You can keep using the app while it runs.")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))
    st.markdown("</div>", unsafe_allow_html=True)
    st.markdown("### Detection Summary")
    cards_html(
        [
            ("Max people", summary.get("people_count_max", 0), "Peak frame count"),
            ("Max vehicles", summary.get("vehicle_count_max", 0), "Peak frame count"),
            ("Movement", summary.get("movement_events", 0), "Triggered events"),
            ("Tracked", len(tracking_ids), "Unique IDs"),
            ("Plates", len(summary.get("plates_detected", [])), "Unique OCR"),
        ]
    )
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("#### Object Counts")
        object_counts = summary.get("object_counts", {})
        if object_counts:
            st.dataframe(
                pd.DataFrame(
                    [{"Object label": label, "Count": count} for label, count in sorted(object_counts.items())]
                ),
                width="stretch",
                hide_index=True,
            )
        else:
            st.info("No object counts saved for this video.")
    with c2:
        st.markdown("#### Plates and Movement")
        st.write("**Plates detected:** " + (", ".join(summary.get("plates_detected", [])) or "None"))
        st.write(f'**Movement events:** {summary.get("movement_events", 0)}')
        st.write("**Tracking IDs:** " + (", ".join(map(str, tracking_ids)) or "None"))
    st.markdown("### Detection Timeline")
    detections = selected.get("detections", [])
    if detections:
        st.dataframe(pd.DataFrame(detections), width="stretch", hide_index=True)
    else:
        st.info("No detection events were recorded for this video.")
    st.markdown("### Captured Objects")
    captured_objects = captured_objects_for_video(selected)
    if captured_objects:
        st.dataframe(
            pd.DataFrame(captured_objects),
            width="stretch",
            hide_index=True,
            column_config={
                "Snapshot preview": st.column_config.ImageColumn("Snapshot preview", width="medium"),
                "Confidence": st.column_config.NumberColumn("Confidence", format="%.1f%%"),
            },
        )
    else:
        st.info("No captured objects were recorded for this video.")


def incidents_page(service):
    header(
        "Incident center",
        "Incidents",
        "Generated from Roadwatch metadata with manual incident support.",
    )
    incident_service = IncidentService()
    stolen_reports = StolenVehicleService().list_reports()
    incidents = incident_service.sync_generated(service.list_videos(), stolen_reports)
    top = st.columns(4)
    top[0].metric("Total incidents", len(incidents))
    top[1].metric("High severity", sum(1 for item in incidents if item.get("severity") == "high"))
    top[2].metric("Open", sum(1 for item in incidents if item.get("status") == "open"))
    top[3].metric("Reviewed", sum(1 for item in incidents if item.get("status") == "reviewed"))
    filters = st.columns(3)
    severity = filters[0].selectbox("Severity", ["All", "low", "medium", "high"])
    incident_type = filters[1].selectbox("Type", ["All"] + INCIDENT_TYPES)
    status = filters[2].selectbox("Status", ["All", "open", "reviewed", "dismissed"])
    filtered = [
        item for item in incidents
        if (severity == "All" or item.get("severity") == severity)
        and (incident_type == "All" or item.get("type") == incident_type)
        and (status == "All" or item.get("status") == status)
    ]
    if filtered:
        archive_cards = []
        for item in filtered[:6]:
            severity_value = item.get("severity", "low")
            tone = "danger" if severity_value == "high" else ("warn" if severity_value == "medium" else "cyan")
            archive_cards.append(
                f"""
                <div class="archive-card">
                  <div class="panel-title" style="margin-bottom:.35rem">{esc(item.get("type", "incident")).replace("_", " ")}</div>
                  <div class="{tone}" style="font-weight:900;letter-spacing:.1em;text-transform:uppercase">{esc(severity_value)}</div>
                  <div class="list-row"><span>Status</span><span>{esc(item.get("status", "open"))}</span></div>
                  <div class="list-row"><span>Time</span><span>{esc(item.get("created_at", "")[:19])}</span></div>
                  <div class="muted" style="margin-top:.55rem">{esc(item.get("description", "Roadwatch incident"))}</div>
                </div>
                """
            )
        st.markdown(
            '<div class="panel-title">Incident Archive Panels</div>'
            + '<div class="archive-grid">'
            + "".join(archive_cards)
            + "</div>",
            unsafe_allow_html=True,
        )
        st.dataframe(pd.DataFrame(filtered), width="stretch", hide_index=True)
        selected = st.selectbox(
            "Update incident",
            [f'{item["incident_id"]} | {item.get("type")}' for item in filtered],
        )
        incident_id = selected.split(" | ")[0]
        action_cols = st.columns(2)
        if action_cols[0].button("Mark reviewed", width="stretch"):
            incident_service.update_status(incident_id, "reviewed")
            st.success("Incident marked reviewed.")
            st.rerun()
        if action_cols[1].button("Dismiss incident", width="stretch"):
            incident_service.update_status(incident_id, "dismissed")
            st.success("Incident dismissed.")
            st.rerun()
    else:
        st.info("No incidents match the selected filters.")
    with st.expander("Create manual incident"):
        videos = service.list_videos()
        labels = {"No video": ""}
        labels.update({f'{item["filename"]} | {item["video_id"]}': item["video_id"] for item in videos})
        with st.form("manual-incident"):
            linked = st.selectbox("Linked video", list(labels))
            sev = st.selectbox("Severity", ["low", "medium", "high"], index=1)
            desc = st.text_area("Description")
            if st.form_submit_button("Create incident", type="primary"):
                incident_service.create_manual(labels[linked], desc, sev)
                st.success("Manual incident created.")


def stolen_vehicle_page(service):
    header(
        "Plate watchlist",
        "Stolen Vehicle",
        "Local stolen vehicle reports and detected OCR plate matching.",
    )
    stolen_service = StolenVehicleService()
    with st.form("stolen-report"):
        st.markdown("### Add Local Report")
        c1, c2, c3 = st.columns(3)
        plate = c1.text_input("Plate number")
        make = c2.text_input("Vehicle make")
        model = c3.text_input("Vehicle model")
        c4, c5, c6 = st.columns(3)
        colour = c4.text_input("Colour")
        case_reference = c5.text_input("Case/reference number")
        status = c6.selectbox("Status", ["active", "recovered", "closed"])
        note = st.text_area("Owner/contact note")
        if st.form_submit_button("Save stolen vehicle report", type="primary"):
            stolen_service.add_report(
                {
                    "plate_number": plate,
                    "vehicle_make": make,
                    "vehicle_model": model,
                    "colour": colour,
                    "owner_contact_note": note,
                    "case_reference": case_reference,
                    "status": status,
                }
            )
            st.success("Stolen vehicle report saved.")
    query = st.text_input("Search reported plates")
    reports = stolen_service.search(query)
    st.markdown("### Local Stolen Vehicle List")
    st.dataframe(pd.DataFrame(reports), width="stretch", hide_index=True)
    matches = stolen_service.match_videos(service.list_videos())
    if matches:
        st.markdown("### Matches Against Detected Plates")
        st.dataframe(pd.DataFrame(matches), width="stretch", hide_index=True)
    else:
        st.info("No detected OCR plates match the local stolen vehicle list.")


def gps_page(service):
    header(
        "Location trail",
        "GPS History",
        "Desktop-safe GPS history with local/mock fallback.",
    )
    gps_service = GPSService()
    status = gps_service.status()
    cards_html(
        [
            ("GPS status", status["status"], "Desktop fallback"),
            ("Latitude", status["latitude"] or "Unavailable", "Latest"),
            ("Longitude", status["longitude"] or "Unavailable", "Latest"),
            ("Speed", f'{status["speed_kmh"]} km/h', "Latest"),
        ]
    )
    st.info("Desktop GPS is unavailable unless you add a local/mock point or connect a real GPS source later.")
    with st.form("mock-gps"):
        c1, c2, c3 = st.columns(3)
        lat = c1.number_input("Latitude", value=0.0, format="%.6f")
        lon = c2.number_input("Longitude", value=0.0, format="%.6f")
        speed = c3.number_input("Speed km/h", value=0.0, min_value=0.0)
        videos = service.list_videos()
        labels = {"No linked video": None}
        labels.update({f'{item["filename"]} | {item["video_id"]}': item["video_id"] for item in videos})
        linked = st.selectbox("Link to video", list(labels))
        if st.form_submit_button("Add mock GPS point"):
            gps_service.add_mock_point(lat, lon, speed, labels[linked])
            st.success("GPS point saved.")
    points = gps_service.list_points()
    if points:
        st.dataframe(pd.DataFrame(points), width="stretch", hide_index=True)


def contacts_page():
    header(
        "SOS roster",
        "Emergency Contacts",
        "Local SOS contact management. Notifications are UI-only until a provider is configured.",
    )
    contact_service = ContactService()
    contacts = contact_service.list_contacts()
    st.metric("Active SOS contacts", contact_service.active_count())
    with st.form("add-contact"):
        c1, c2, c3, c4 = st.columns([1.3, 1, 1, .8])
        name = c1.text_input("Name")
        phone = c2.text_input("Phone number")
        relationship = c3.text_input("Relationship")
        active = c4.checkbox("Active for SOS", value=True)
        if st.form_submit_button("Add contact", type="primary"):
            contact_service.add_contact(name, phone, relationship, active)
            st.success("Contact added.")
    if contacts:
        edited = st.data_editor(
            pd.DataFrame(contacts),
            width="stretch",
            num_rows="dynamic",
            key="contacts-editor",
        )
        c1, c2 = st.columns(2)
        if c1.button("Save contact changes", width="stretch"):
            contact_service.update_contacts(edited.to_dict("records"))
            st.success("Contacts saved.")
        delete_id = c2.selectbox(
            "Delete contact",
            [""] + [f'{item["contact_id"]} | {item.get("name")}' for item in contacts],
        )
        if delete_id and c2.button("Delete selected contact", width="stretch"):
            contact_id = delete_id.split(" | ")[0]
            contact_service.update_contacts(
                [item for item in contacts if item.get("contact_id") != contact_id]
            )
            st.success("Contact deleted.")
    else:
        st.info("No emergency contacts saved yet.")


def vehicle_profile_page():
    header(
        "Fleet identity",
        "Vehicle Profile",
        "Local vehicle profile used by reports, incidents, and exported metadata.",
    )
    service = VehicleProfileService()
    profile = service.get_profile()
    with st.form("vehicle-profile"):
        c1, c2, c3 = st.columns(3)
        make = c1.text_input("Vehicle make", profile["vehicle_make"])
        model = c2.text_input("Model", profile["vehicle_model"])
        year = c3.text_input("Year", profile["year"])
        c4, c5, c6 = st.columns(3)
        colour = c4.text_input("Colour", profile["colour"])
        plate = c5.text_input("Registration/plate number", profile["registration_plate"])
        driver = c6.text_input("Driver name", profile["driver_name"])
        fleet = st.text_input("Company/fleet name", profile["company_fleet_name"])
        if st.form_submit_button("Save vehicle profile", type="primary"):
            service.save_profile(
                {
                    "vehicle_make": make,
                    "vehicle_model": model,
                    "year": year,
                    "colour": colour,
                    "registration_plate": plate,
                    "driver_name": driver,
                    "company_fleet_name": fleet,
                }
            )
            st.success("Vehicle profile saved.")


def analytics_page(service):
    header(
        "Fleet analytics",
        "Analytics",
        "Roadwatch detections, incidents, plates, movement, and processing health.",
    )
    videos = service.list_videos()
    incidents = IncidentService().sync_generated(videos, StolenVehicleService().list_reports())
    object_counts = Counter()
    recordings_by_day = Counter()
    plates_by_day = Counter()
    movement_by_recording = {}
    for video in videos:
        day = video.get("started_at", "")[:10] or "Unknown"
        recordings_by_day[day] += 1
        summary = video.get("objects_summary", {})
        object_counts.update(summary.get("object_counts", {}))
        plates_by_day[day] += len(summary.get("plates_detected", []))
        movement_by_recording[video.get("filename")] = summary.get("movement_events", 0)
    total_objects = sum(object_counts.values())
    people = object_counts.get("person", 0)
    vehicles = sum(object_counts.get(label, 0) for label in ["car", "truck", "bus", "motorcycle", "bicycle"])
    high = sum(1 for item in incidents if item.get("severity") == "high")
    processing_errors = sum(1 for item in videos if item.get("processing_error"))
    avg_duration = (
        sum(item.get("duration_seconds", 0) for item in videos) / len(videos)
        if videos else 0
    )
    cards_html(
        [
            ("Recordings", len(videos), "Total saved"),
            ("Processed", sum(1 for item in videos if item.get("processed_video_path")), "Annotated videos"),
            ("Incidents", len(incidents), "Generated/local"),
            ("High severity", high, "Needs review"),
            ("Objects", total_objects, "Detected/tracked"),
            ("People", people, "Detected"),
            ("Vehicles", vehicles, "Detected"),
            ("Plates", sum(plates_by_day.values()), "Detected"),
            ("Movement", sum(movement_by_recording.values()), "Events"),
            ("Processing errors", processing_errors, "Post-processing"),
            ("Avg duration", format_duration(avg_duration), "Recordings"),
        ]
    )
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("### Detections by Object Type")
        if object_counts:
            st.bar_chart(pd.Series(dict(object_counts)))
        st.markdown("### Recordings by Day")
        if recordings_by_day:
            st.bar_chart(pd.Series(dict(recordings_by_day)))
    with c2:
        st.markdown("### Incidents by Severity")
        severity_counts = Counter(item.get("severity", "unknown") for item in incidents)
        if severity_counts:
            st.bar_chart(pd.Series(dict(severity_counts)))
        st.markdown("### Movement Events by Recording")
        if movement_by_recording:
            st.bar_chart(pd.Series(movement_by_recording))
    if plates_by_day:
        st.markdown("### Plates Detected by Day")
        st.line_chart(pd.Series(dict(plates_by_day)))


def settings_page(settings):
    header(
        "System configuration",
        "Settings",
        "Camera, AI, storage, and sync placeholders. Secrets belong in .env, not here.",
    )
    with st.form("settings"):
        with st.expander("Camera Settings", expanded=True):
            camera_id = st.text_input("Camera ID", settings["camera_id"])
            c1, c2, c3, c4 = st.columns(4)
            camera_index = c1.number_input("Camera index", 0, 10, settings["camera_index"])
            width = c2.number_input("Width", 320, 1920, settings["camera_width"], 160)
            height = c3.number_input("Height", 240, 1080, settings["camera_height"], 120)
            fps = c4.number_input("Target FPS", 7, 30, settings["target_camera_fps"])
            preview_fps = st.slider("Preview FPS", 1, 15, int(settings["streamlit_preview_fps"]))
        with st.expander("AI Settings", expanded=True):
            mode = st.selectbox(
                "Performance mode",
                list(PERFORMANCE_MODES),
                index=list(PERFORMANCE_MODES).index(settings["performance_mode"]),
            )
            interval = st.slider("AI processing interval seconds", 0.25, 5.0, float(settings["ai_process_interval_seconds"]), 0.25)
            confidence = st.slider("YOLO confidence", 0.1, 0.9, float(settings["confidence"]), 0.05)
            tracking = st.toggle("Enable tracking", settings["enable_tracking"])
            ocr = st.toggle("Enable OCR", settings["enable_ocr"])
            movement = st.toggle("Enable movement detection", settings.get("enable_movement", True))
            snapshots = st.toggle("Save snapshots", settings["save_snapshots"])
            annotated = st.toggle("Save processed annotated video", settings["save_annotated_video"])
        with st.expander("Storage Settings", expanded=False):
            st.dataframe(pd.DataFrame(StorageService().status()), width="stretch", hide_index=True)
        with st.expander("Sync/API Settings", expanded=False):
            sync_url = st.text_input("Sync URL", settings["sync_url"])
            sync_key = st.text_input("Sync API key placeholder", settings["sync_api_key"], type="password")
            ai_api_key = st.text_input("AI API key placeholder", settings.get("ai_api_key", ""), type="password")
            supabase_url = st.text_input("Supabase URL placeholder", settings.get("supabase_url", ""))
            supabase_key = st.text_input("Supabase anon key placeholder", settings.get("supabase_anon_key", ""), type="password")
            supabase_bucket = st.text_input("Supabase bucket placeholder", settings.get("supabase_bucket", "videos"))
            firebase_push_url = st.text_input("Firebase push URL", settings.get("firebase_push_url", ""))
            firebase_api_key = st.text_input("Firebase API key placeholder", settings.get("firebase_api_key", ""), type="password")
        if st.form_submit_button("Save settings", type="primary"):
            updated = {
                **settings,
                "camera_id": camera_id,
                "camera_index": int(camera_index),
                "camera_width": int(width),
                "camera_height": int(height),
                "target_camera_fps": int(fps),
                "streamlit_preview_fps": int(preview_fps),
                "performance_mode": mode,
                **PERFORMANCE_MODES[mode],
                "ai_process_interval_seconds": interval,
                "confidence": confidence,
                "enable_tracking": tracking,
                "enable_ocr": ocr,
                "enable_movement": movement,
                "save_snapshots": snapshots,
                "save_annotated_video": annotated,
                "sync_url": sync_url,
                "sync_api_key": sync_key,
                "ai_api_key": ai_api_key,
                "supabase_url": supabase_url,
                "supabase_anon_key": supabase_key,
                "supabase_bucket": supabase_bucket,
                "firebase_push_url": firebase_push_url,
                "firebase_api_key": firebase_api_key,
            }
            save_settings(updated)
            st.success("Settings saved.")
            st.rerun()
    supabase = SupabaseService(
        settings.get("supabase_url", ""),
        settings.get("supabase_anon_key", ""),
        settings.get("supabase_bucket", "videos"),
    )
    st.info(supabase.status()["message"])


def main():
    st.set_page_config(
        page_title="Roadwatch Vision Recorder",
        page_icon="RV",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    styles()
    settings = load_settings()
    service = VideoService()
    sidebar_brand("Dash Cam")
    nav_options = [
        "Live Dash Cam",
        "Videos",
        "Incidents",
        "Stolen Vehicles",
        "GPS History",
        "Emergency Contacts",
        "Vehicle Profile",
        "Analytics",
        "Settings",
    ]
    if st.session_state.get("main_navigation") not in nav_options:
        st.session_state.main_navigation = nav_options[0]
    page = st.sidebar.radio(
        "Workspace",
        nav_options,
        key="main_navigation",
        label_visibility="collapsed",
    )
    sidebar_status(settings)
    system_top_bar(settings)
    {
        "Live Dash Cam": lambda: dash_cam_page(settings),
        "Videos": lambda: videos_page(service, settings),
        "Incidents": lambda: incidents_page(service),
        "Stolen Vehicles": lambda: stolen_vehicle_page(service),
        "GPS History": lambda: gps_page(service),
        "Emergency Contacts": contacts_page,
        "Vehicle Profile": vehicle_profile_page,
        "Analytics": lambda: analytics_page(service),
        "Settings": lambda: settings_page(settings),
    }[page]()


if __name__ == "__main__":
    main()
