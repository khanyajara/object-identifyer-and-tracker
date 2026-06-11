import base64
import json
import os
from pathlib import Path

import pandas as pd
import streamlit as st

from core.detector import load_yolo_model
from core.opencv_recorder import OpenCVVisionRecorder
from core.ocr import load_ocr_reader
from services.report_service import build_summary, videos_dataframe
from services.sync_service import SyncService
from services.video_service import VideoService


PROJECT_DIR = Path(__file__).resolve().parent
SETTINGS_PATH = PROJECT_DIR / "settings.json"
os.environ.setdefault("YOLO_CONFIG_DIR", str(PROJECT_DIR / "Ultralytics"))
os.environ.setdefault("MPLCONFIGDIR", str(PROJECT_DIR / "Ultralytics"))

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
    "ocr_interval_seconds": 5,
    "save_snapshots": False,
    "save_annotated_video": False,
    "sync_url": "",
    "sync_api_key": "",
}


def load_settings():
    if SETTINGS_PATH.exists():
        try:
            return {**DEFAULTS, **json.loads(SETTINGS_PATH.read_text("utf-8"))}
        except (OSError, json.JSONDecodeError):
            pass
    return DEFAULTS.copy()


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
        .stApp{background:linear-gradient(145deg,#07101d,#0b1525);color:#f8fafc}
        [data-testid=stSidebar]{background:#060d18}
        .block-container{max-width:1450px;padding-top:1.5rem}
        .eyebrow{color:#ff574b;font-weight:800;letter-spacing:.14em;text-transform:uppercase;font-size:.72rem}
        .hero{font-size:clamp(2rem,4vw,3.6rem);font-weight:850;letter-spacing:-.05em;margin:0}
        .muted{color:#94a3b8}
        .panel{background:#101a2a;border:1px solid rgba(148,163,184,.18);border-radius:16px;padding:1rem}
        .metric-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.7rem}
        .metric-card{background:#101a2a;border:1px solid rgba(148,163,184,.18);border-radius:14px;padding:.9rem}
        .metric-label{color:#94a3b8;font-size:.7rem;font-weight:800;letter-spacing:.1em}
        .metric-value{font-size:1.55rem;font-weight:800}
        [data-testid="stImage"] img{
            width:100% !important;
            max-height:760px !important;
            object-fit:contain !important;
            background:#020617;
            border-radius:18px;
        }
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


def metric_html(recorder, metrics):
    values = [
        ("ACTIVE RESOLUTION", metrics.get("active_resolution", "Waiting")),
        ("CAMERA FPS", f'{metrics["camera_fps"]:.1f}'),
        ("AI FPS", f'{metrics["ai_fps"]:.1f}'),
        ("PROCESSING", f'{metrics["processing_time_ms"]:.0f} ms'),
        ("RECORDED", f'{metrics["recorded_frames"]:,}'),
        ("AI DROPPED", f'{metrics["ai_dropped_samples"]:,}'),
    ]
    cards = "".join(
        f'<div class="metric-card"><div class="metric-label">{label}</div>'
        f'<div class="metric-value">{value}</div></div>'
        for label, value in values
    )
    return f'<div class="metric-grid">{cards}</div>'


def live_page(settings):
    header(
        "Vision control",
        "Live Recorder",
        "OpenCV capture, original-quality recording, and sampled AI analysis.",
    )
    recorder = st.session_state.get("recorder")
    active = bool(recorder and recorder.active)
    left, middle, status = st.columns([1.2, 1.2, 5])
    with left:
        if st.button(
            "Start Recording",
            type="primary",
            disabled=active,
            use_container_width=True,
        ):
            try:
                recorder = OpenCVVisionRecorder(
                    settings,
                    cached_model(settings["model_name"]),
                    cached_ocr() if settings["enable_ocr"] else None,
                )
                st.session_state.recorder = recorder
                st.session_state.last_record = None
                st.rerun()
            except Exception as exc:
                st.error(str(exc))
    with middle:
        if st.button(
            "Stop Recording",
            disabled=not active,
            use_container_width=True,
        ):
            st.session_state.last_record = recorder.stop()
            st.session_state.recorder = None
            st.rerun()
    with status:
        st.info(
            "REC - camera, recording, detection, tracking, OCR, and logging active"
            if active
            else "Ready - press Start Recording to open the camera"
        )

    frame_placeholder = st.empty()
    if not active:
        frame_placeholder.markdown(
            """
            <div class="panel" style="min-height:620px;display:grid;place-items:center;text-align:center">
                <div>
                    <div style="font-size:2rem;font-weight:800">Camera ready</div>
                    <p class="muted">Press Start Recording to open the local OpenCV camera and begin recording with all AI features.</p>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    profile_columns = st.columns(6)
    profile_values = [
        ("Source", f'OpenCV camera {settings["camera_index"]}'),
        ("Default HD", f'{settings["camera_width"]} x {settings["camera_height"]}'),
        ("Requested", f'{settings["target_camera_fps"]} FPS'),
        ("AI interval", f'{settings["ai_process_interval_seconds"]:.1f}s'),
        ("Tracking", "On" if settings["enable_tracking"] else "Off"),
        ("OCR", "On" if settings["enable_ocr"] else "Off"),
    ]
    for column, (label, value) in zip(profile_columns, profile_values):
        column.metric(label, value)

    if active:
        metrics_box = st.empty()
        live_panel = st.empty()
        objects_box = st.empty()
        error_box = st.empty()

        @st.fragment(run_every=1 / settings["streamlit_preview_fps"])
        def status_fragment():
            current = st.session_state.get("recorder")
            if not current or not current.active:
                return
            frame, event, metrics, error = current.get_dashboard_state()
            if frame is not None:
                frame_placeholder.image(
                    frame,
                    channels="BGR",
                    use_container_width=True,
                )
            metrics_box.html(metric_html(current, metrics))
            if event:
                plate_text = event.get("plate_text") or "None"
                movement = (
                    "Detected" if event["movement_detected"] else "Still"
                )
                live_panel.html(
                    '<div class="metric-grid">'
                    f'<div class="metric-card"><div class="metric-label">PEOPLE</div><div class="metric-value">{event["people_count"]}</div></div>'
                    f'<div class="metric-card"><div class="metric-label">VEHICLES</div><div class="metric-value">{event["vehicle_count"]}</div></div>'
                    f'<div class="metric-card"><div class="metric-label">PLATE</div><div class="metric-value" style="font-size:1.05rem">{plate_text}</div></div>'
                    f'<div class="metric-card"><div class="metric-label">MOVEMENT</div><div class="metric-value" style="font-size:1.05rem">{movement}</div></div>'
                    f'<div class="metric-card"><div class="metric-label">AI / PROCESSING</div><div class="metric-value" style="font-size:1.05rem">{metrics["ai_fps"]:.1f} FPS / {metrics["processing_time_ms"]:.0f} ms</div></div>'
                    "</div>"
                )
                object_labels = [
                    (
                        f'{item["label"]} '
                        f'{"ID " + str(item["tracking_id"]) if item.get("tracking_id") is not None else "untracked"} '
                        f'({item.get("confidence", 0):.1f}%)'
                    )
                    for item in event.get("objects", [])
                ]
                objects_box.markdown(
                    "**Latest detected objects:** "
                    + (", ".join(object_labels) if object_labels else "None")
                )
            if error:
                error_box.warning(error)
            else:
                error_box.empty()

        status_fragment()
    if st.session_state.get("last_record"):
        record = st.session_state.last_record
        st.success(f'Saved {record["filename"]}.')


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
            box = item.get("box", {})
            captured.append(
                {
                    "Time": detection.get("timestamp"),
                    "Frame number": detection.get("frame_number"),
                    "Object label": item.get("label"),
                    "Tracking ID": item.get("tracking_id"),
                    "Confidence": round(
                        float(item.get("confidence") or 0), 1
                    ),
                    "Bounding box": (
                        f'x={box.get("x")}, y={box.get("y")}, '
                        f'w={box.get("width")}, h={box.get("height")}'
                    ),
                    "Plate text": detection.get("plate_text"),
                    "Movement detected": detection.get(
                        "movement_detected", False
                    ),
                    "Snapshot preview": snapshot,
                }
            )
    return captured


def videos_page(service, settings):
    header(
        "Evidence library",
        "Recorded Videos",
        "Open a video to see only the objects and detections linked to it.",
    )
    videos = service.list_videos()
    if not videos:
        st.info("No recordings yet.")
        return
    st.dataframe(videos_dataframe(videos), use_container_width=True, hide_index=True)
    labels = {
        f'{video["filename"]} | {video.get("started_at", "")[:16]}':
        video["video_id"]
        for video in videos
    }
    selected_video_id = labels[st.selectbox("Open recording", list(labels))]
    selected = service.load(selected_video_id)
    summary = selected.get("objects_summary", {})
    tracking_ids = unique_tracking_ids(selected)
    left, right = st.columns([2, 1])
    with left:
        path = Path(selected["video_path"])
        if path.exists() and path.stat().st_size:
            st.video(str(path))
        else:
            st.warning("Video file unavailable; metadata is preserved.")
    with right:
        st.metric("Duration", f'{selected.get("duration_seconds",0):.1f}s')
        st.metric("Detection events", len(selected.get("detections", [])))
        st.metric("Unique tracked objects", len(tracking_ids))
        if st.button("Sync this video"):
            try:
                SyncService(settings["sync_url"], settings["sync_api_key"]).sync(selected)
                service.mark_synced(selected["video_id"], "Synced")
                st.success("Synced.")
            except Exception as exc:
                st.error(str(exc))

    st.markdown("### Detection Summary")
    summary_columns = st.columns(5)
    summary_values = [
        ("Max people", summary.get("people_count_max", 0)),
        ("Max vehicles", summary.get("vehicle_count_max", 0)),
        ("Movement events", summary.get("movement_events", 0)),
        ("Tracked IDs", len(tracking_ids)),
        ("Plates", len(summary.get("plates_detected", []))),
    ]
    for column, (label, value) in zip(summary_columns, summary_values):
        column.metric(label, value)

    object_counts = summary.get("object_counts", {})
    detail_left, detail_right = st.columns(2)
    with detail_left:
        st.markdown("#### Object Counts")
        if object_counts:
            st.dataframe(
                pd.DataFrame(
                    [
                        {"Object label": label, "Count": count}
                        for label, count in sorted(object_counts.items())
                    ]
                ),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("No object counts were saved for this video.")
    with detail_right:
        st.markdown("#### Plates and Movement")
        plates = summary.get("plates_detected", [])
        st.write("**Plates detected:** " + (", ".join(plates) or "None"))
        st.write(
            f'**Movement events:** {summary.get("movement_events", 0)}'
        )
        st.write(
            "**Tracking IDs:** "
            + (", ".join(map(str, tracking_ids)) or "None")
        )

    st.markdown("### Detection timeline")
    detections = selected.get("detections", [])
    if detections:
        st.dataframe(
            pd.DataFrame(detections),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No detection events were recorded for this video.")

    st.markdown("### Captured Objects for This Video")
    captured_objects = captured_objects_for_video(selected)
    if captured_objects:
        st.dataframe(
            pd.DataFrame(captured_objects),
            use_container_width=True,
            hide_index=True,
            column_config={
                "Snapshot preview": st.column_config.ImageColumn(
                    "Snapshot preview",
                    help="Snapshot saved for this detection event.",
                    width="medium",
                ),
                "Confidence": st.column_config.NumberColumn(
                    "Confidence", format="%.1f%%"
                ),
            },
        )
    else:
        st.info("No captured objects were recorded for this video.")


def logs_page(service):
    header("Detection archive", "Detection Logs", "All events remain attached to their source video.")
    videos = service.list_videos()
    for video in videos:
        with st.expander(f'{video["filename"]} | {len(video.get("detections",[]))} events'):
            st.dataframe(pd.DataFrame(video.get("detections", [])), use_container_width=True)


def reports_page(service):
    header("Intelligence summary", "Reports", "Aggregate activity across saved recordings.")
    videos = service.list_videos()
    summary = build_summary(videos)
    columns = st.columns(4)
    for column, (label, value) in zip(columns, summary.items()):
        column.metric(label.title(), value)
    counts = {}
    for video in videos:
        for label, count in video.get("objects_summary", {}).get("object_counts", {}).items():
            counts[label] = max(counts.get(label, 0), count)
    if counts:
        st.bar_chart(pd.Series(counts, name="Peak objects"))


def settings_page(settings):
    header("System configuration", "Settings", "Tune OpenCV capture and asynchronous vision analysis.")
    with st.form("settings"):
        camera_id = st.text_input("Camera ID", settings["camera_id"])
        c1, c2, c3, c4 = st.columns(4)
        camera_index = c1.number_input(
            "Camera index", 0, 10, settings["camera_index"]
        )
        width = c2.number_input("Width", 320, 1920, settings["camera_width"], 160)
        height = c3.number_input("Height", 240, 1080, settings["camera_height"], 120)
        fps = c4.number_input("Requested FPS", 7, 30, settings["target_camera_fps"])
        mode = st.selectbox(
            "Performance mode",
            list(PERFORMANCE_MODES),
            index=list(PERFORMANCE_MODES).index(settings["performance_mode"]),
        )
        interval = st.slider(
            "AI interval (seconds)", 0.25, 5.0,
            float(settings["ai_process_interval_seconds"]), 0.25
        )
        confidence = st.slider("Confidence", 0.1, 0.9, float(settings["confidence"]), 0.05)
        tracking = st.toggle("Enable tracking", settings["enable_tracking"])
        ocr = st.toggle("Enable plate OCR", settings["enable_ocr"])
        snapshots = st.toggle("Save snapshots", settings["save_snapshots"])
        sync_url = st.text_input("Dashcam sync URL", settings["sync_url"])
        sync_key = st.text_input("Sync API key", settings["sync_api_key"], type="password")
        if st.form_submit_button("Save settings", type="primary"):
            updated = {
                **settings,
                "camera_id": camera_id,
                "camera_index": int(camera_index),
                "camera_width": int(width),
                "camera_height": int(height),
                "target_camera_fps": int(fps),
                "performance_mode": mode,
                **PERFORMANCE_MODES[mode],
                "ai_process_interval_seconds": interval,
                "confidence": confidence,
                "enable_tracking": tracking,
                "enable_ocr": ocr,
                "save_snapshots": snapshots,
                "sync_url": sync_url,
                "sync_api_key": sync_key,
            }
            save_settings(updated)
            st.success("Settings saved.")
            st.rerun()


def main():
    st.set_page_config(
        page_title="Roadwatch Vision Recorder",
        page_icon="RV",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    styles()
    settings = load_settings()
    service = VideoService()
    st.sidebar.markdown("## Roadwatch\nVision Recorder")
    page = st.sidebar.radio(
        "Workspace",
        ["Live Recorder", "Recorded Videos", "Detection Logs", "Reports", "Settings"],
        label_visibility="collapsed",
    )
    recorder = st.session_state.get("recorder")
    st.sidebar.caption("Recorder active" if recorder and recorder.active else "Camera node ready")
    {
        "Live Recorder": lambda: live_page(settings),
        "Recorded Videos": lambda: videos_page(service, settings),
        "Detection Logs": lambda: logs_page(service),
        "Reports": lambda: reports_page(service),
        "Settings": lambda: settings_page(settings),
    }[page]()


if __name__ == "__main__":
    main()
