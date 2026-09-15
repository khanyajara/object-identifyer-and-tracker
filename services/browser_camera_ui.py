"""Browser permission/device selection and session-scoped WebRTC callbacks."""
import json
import os
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
    enabled = st.session_state.get("browser_preview_enabled", True)
    def processor_factory(channel):
        class Processor(VideoProcessorBase):
            def __init__(self):
                self.source = channel.new_connection()

            def recv(self, frame):
                self.source.offer(frame.to_ndarray(format="bgr24"))
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
            media_toggle_controls=False,
        )

    # Transport lives inside the existing preview, without a second video or
    # camera-selection panel. The normal dashboard renders received frames.
    render_feed("rear", enabled)


def refresh_browser_capture(manager):
    """Advance capture from the dashboard's existing timer, without app reruns."""
    for channel in manager.channels.values():
        if channel.browser_source.ready and not channel.is_live:
            channel.preview_ai_enabled = True
            channel.ensure_capture()
