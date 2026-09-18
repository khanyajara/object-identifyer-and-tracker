"""Browser permission/device selection and session-scoped WebRTC callbacks."""
import json
import os
import logging
from uuid import uuid4


def rtc_configuration():
    raw = os.getenv("ROADWATCH_WEBRTC_ICE_SERVERS", "").strip()
    servers = json.loads(raw) if raw else [{"urls": ["stun:stun.l.google.com:19302"]}]
    if not isinstance(servers, list) or not servers:
        raise ValueError("ICE servers must be a nonempty JSON array")
    for server in servers:
        if not isinstance(server, dict):
            raise ValueError("Invalid ICE server")
        urls = server.get("urls")
        urls = [urls] if isinstance(urls, str) else urls
        if not isinstance(urls, list) or not urls or not all(isinstance(url, str) and url.startswith(("stun:", "stuns:", "turn:", "turns:")) for url in urls):
            raise ValueError("Invalid ICE server URL")
    return {"iceServers": servers}


def render_browser_cameras(manager):
    import streamlit as st
    try:
        from streamlit_webrtc import VideoProcessorBase, WebRtcMode, webrtc_streamer
    except ImportError:
        st.error("Browser camera support is not installed. Redeploy with the updated requirements.txt.")
        return
    try:
        rtc = rtc_configuration()
    except (ValueError, TypeError):
        st.error("Browser camera connection settings are invalid. Ask the administrator to check the camera relay configuration.")
        return
    if not hasattr(manager, "browser_widget_id"):
        manager.browser_widget_id = uuid4().hex
        logging.getLogger("roadwatch.camera").info("[Camera] backend selected: browser (session scoped)")
    enabled = st.session_state.get("browser_preview_enabled", True)
    def processor_factory(channel):
        class Processor(VideoProcessorBase):
            def __init__(self):
                self.source = channel.new_connection()

            def recv(self, frame):
                try:
                    self.source.offer(frame.to_ndarray(format="bgr24"))
                except Exception as exc:
                    if self.source.last_error is None:
                        logging.getLogger("roadwatch.camera").exception("[Camera] browser frame conversion failed")
                    self.source.last_error = str(exc)
                return frame

            def on_ended(self):
                self.source.release()
        return Processor

    def render_feed(role, desired):
        return webrtc_streamer(
            key="roadwatch-" + manager.browser_widget_id + "-" + role,
            mode=WebRtcMode.SENDONLY,
            video_processor_factory=processor_factory(manager.channel(role)),
            rtc_configuration=rtc,
            media_stream_constraints={"video": {"width": {"ideal": 640}, "height": {"ideal": 480}, "frameRate": {"ideal": 15, "max": 15}}, "audio": False},
            desired_playing_state=desired,
            async_processing=True,
            video_receiver_size=1,
            media_toggle_controls=False,
        )

    # Transport lives inside the existing preview, without a second video or
    # camera-selection panel. The normal dashboard renders received frames.
    context = render_feed("rear", enabled)

    @st.fragment(run_every=1)
    def health_status():
        if not enabled:
            st.caption("Camera stopped. Select Open / Retry Preview to reconnect.")
            return
        health = manager.channel("rear").browser_source.health()
        status = health["status"]
        if status == "camera_active":
            st.caption(f"Camera active · {health['width']}×{health['height']} · {health['frames_received']} frames received · {health['frames_dropped']} older frames dropped")
        elif status == "camera_error":
            st.warning("Camera connection timed out. Allow camera access in this site's browser settings, close other apps using the camera, then select Open / Retry Preview. If permission is allowed but no frames arrive, the administrator may need to configure a TURN relay.")
        elif status in {"camera_stale", "camera_reconnecting"}:
            st.warning("Camera stream interrupted. Waiting for fresh frames; if it does not recover, stop any recording and select Open / Retry Preview.")
        elif context.state.signalling or context.state.playing:
            st.info("Camera starting. Waiting for the first video frame.")
        else:
            st.info("Camera permission required. Allow camera access for this site. If access is blocked, allow it in browser settings and reload Roadwatch. If no camera is found, connect one; if it is busy, close other camera apps.")
        if health["last_error"]:
            st.error("Camera frame processing failed. Retry preview and check the server camera logs.")

    health_status()


def refresh_browser_capture(manager):
    """Advance capture from the dashboard's existing timer, without app reruns."""
    for channel in manager.channels.values():
        if channel.browser_source.ready and not channel.is_live:
            channel.preview_ai_enabled = True
            channel.ensure_capture()
    manager.ensure_browser_driver()
