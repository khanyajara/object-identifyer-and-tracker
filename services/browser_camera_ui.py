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
    st.caption("Select PC Camera or DroidCam Video, allow camera access, then press START on either feed. This connects the preview; Start Recording saves video.")

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

    for column, role, label in zip(st.columns(2), ("front", "rear"), ("Main camera", "Cabin camera (optional)")):
        with column:
            st.markdown("**" + label + "**")
            webrtc_streamer(
                key="roadwatch-" + manager.browser_widget_id + "-" + role,
                mode=WebRtcMode.SENDRECV,
                video_processor_factory=processor_factory(manager.channel(role)),
                rtc_configuration=rtc,
                media_stream_constraints={"video": {"width": {"ideal": 640}, "height": {"ideal": 480}, "frameRate": {"ideal": 15, "max": 15}}, "audio": False},
                async_processing=True,
            )
    with st.expander("Camera connection help"):
        st.write("Use HTTPS (or localhost). Choose different devices for the two feeds. Keep DroidCam running on your PC if you select it. If the picture remains black, first check it in the Windows Camera app.")
        st.write("If the connection stays on connecting or fails on a work/mobile network, the administrator may need to configure a TURN relay in ROADWATCH_WEBRTC_ICE_SERVERS. A public STUN server alone does not work on every hosting network.")

    @st.fragment(run_every=1)
    def connection_status():
        previous = manager.preview_active
        for channel in manager.channels.values():
            if channel.browser_source.ready and not channel.is_live:
                channel.preview_ai_enabled = True
                channel.ensure_capture()
        ready = [channel.config.label for channel in manager.channels.values() if channel.browser_source.ready]
        st.caption("Receiving: " + ", ".join(ready) if ready else "Waiting for a browser camera connection.")
        if manager.is_recording and not any(channel.is_recording for channel in manager.channels.values()):
            st.warning("Camera streams ended. Press Stop Recording to finish processing the recorded portion.")
        if previous != manager.preview_active:
            st.rerun(scope="app")
    connection_status()
