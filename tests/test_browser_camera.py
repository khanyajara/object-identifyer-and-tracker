import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import numpy as np

from core.browser_camera import BrowserFrameSource, BrowserCameraChannel
from core.capture_mode import browser_capture_enabled
from core.dual_camera import CameraConfig, DualCameraManager
from services.browser_camera_ui import rtc_configuration


class BrowserCameraTests(unittest.TestCase):
    def test_refresh_starts_only_ready_idle_channels(self):
        from services.browser_camera_ui import refresh_browser_capture
        manager = Mock()
        ready, waiting, live = Mock(), Mock(), Mock()
        ready.browser_source.ready, ready.is_live = True, False
        waiting.browser_source.ready, waiting.is_live = False, False
        live.browser_source.ready, live.is_live = True, True
        manager.channels = {"ready": ready, "waiting": waiting, "live": live}
        refresh_browser_capture(manager)
        ready.ensure_capture.assert_called_once()
        self.assertTrue(ready.preview_ai_enabled)
        waiting.ensure_capture.assert_not_called()
        live.ensure_capture.assert_not_called()

    def test_dashboard_tick_connects_without_requesting_app_rerun(self):
        # Exercise the actual nested dashboard tick with UI/storage boundaries mocked.
        import ast
        tree = ast.parse(Path("streamlit_app.py").read_text(encoding="utf-8"))
        function = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "dash_fragment")
        function.decorator_list = []
        module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
        current = Mock(active=False, preview_active=False)
        current.manager.is_recording = False
        st = Mock()
        class State(dict):
            __setattr__ = dict.__setitem__
        st.session_state = State(camera_manager=current, admin_token="test")
        placeholder = Mock()
        namespace = {"st": st, "AdminAuthService": Mock(), "frame_placeholder": placeholder,
                     "browser_mode": True, "time": time, "format_duration": str,
                     "render_static_hud": Mock()}
        exec(compile(module, "streamlit_app.py", "exec"), namespace)
        with patch("services.browser_camera_ui.refresh_browser_capture") as refresh:
            namespace["dash_fragment"]()
            refresh.assert_called_once_with(current.manager)
            current.preview_active = True
            current.get_dashboard_state.return_value = (np.zeros((2,2,3)), {}, {}, None)
            namespace["dash_fragment"]()
            placeholder.image.assert_called_once()
            st.rerun.assert_not_called()
            namespace["AdminAuthService"].return_value.decode_access_token.side_effect = ValueError("expired")
            namespace["dash_fragment"]()
            st.error.assert_called_once()
            st.rerun.assert_not_called()

    def test_cloud_mode_and_local_override(self):
        with patch("core.capture_mode.platform.system", return_value="Linux"), patch("core.capture_mode.Path.glob", return_value=iter([])), patch.dict(os.environ, {"CAMERA_INPUT_MODE": "auto"}):
            self.assertTrue(browser_capture_enabled())
            self.assertFalse(browser_capture_enabled({"camera_input_mode": "local"}))
        self.assertTrue(browser_capture_enabled({"camera_input_mode": "browser"}))

    def test_latest_only_queue_and_stale_frames(self):
        source = BrowserFrameSource(8, 8, 15)
        self.assertFalse(source.ready)
        for value in range(20):
            source.offer(np.full((8, 8, 3), value, dtype=np.uint8))
        ok, frame = source.read()
        self.assertTrue(ok)
        self.assertEqual(int(frame[0, 0, 0]), 19)
        self.assertEqual(source.dropped, 19)
        source.last_received = time.monotonic()-3
        self.assertFalse(source.ready)
        source.release()
        self.assertEqual(source.read(), (False, None))

    def test_no_server_capture_or_discovery_in_browser_mode(self):
        with patch("core.dual_camera.cv2.VideoCapture") as capture, patch("core.dual_camera.windows_camera_devices") as discover:
            manager = DualCameraManager.from_settings({"camera_input_mode": "browser"})
            self.assertFalse(manager.start_preview())
            manager.release()
            capture.assert_not_called()
            discover.assert_not_called()

    def test_browser_sessions_never_share_cameras(self):
        first = DualCameraManager.from_settings({"camera_input_mode": "browser"})
        second = DualCameraManager.from_settings({"camera_input_mode": "browser"})
        first.channel("front").browser_source.offer(np.zeros((8,8,3), dtype=np.uint8))
        self.assertFalse(second.channel("front").browser_source.ready)
        self.assertIsNot(first.channel("front"), second.channel("front"))
        first.release(); second.release()

    def test_old_peer_cannot_close_replacement(self):
        channel = BrowserCameraChannel(CameraConfig(index=0))
        old = channel.new_connection()
        new = channel.new_connection()
        old.release()
        new.offer(np.zeros((8,8,3), dtype=np.uint8))
        self.assertTrue(new.ready)
        self.assertFalse(old.ready)
        channel.release()

    def test_single_browser_feed_records_using_existing_writer(self):
        with tempfile.TemporaryDirectory(dir=".") as directory:
            manager = DualCameraManager.from_settings({"camera_input_mode": "browser"}, video_dir=directory)
            channel = manager.channel("rear")
            writer = Mock()
            running = threading.Event()
            running.set()
            def feed():
                while running.is_set():
                    channel.browser_source.offer(np.zeros((48,64,3), dtype=np.uint8))
                    time.sleep(.02)
            thread = threading.Thread(target=feed)
            thread.start()
            try:
                deadline=time.monotonic()+2
                while not channel.browser_source.ready and time.monotonic()<deadline:
                    time.sleep(.01)
                with patch("core.dual_camera.open_video_writer", return_value=(writer, "VP80")), patch("services.driver_monitoring.runtime.recording_context") as context, patch("services.driver_monitoring.runtime.identity_metadata", return_value={"driver_id":"other-user"}) as identity:
                    self.assertTrue(manager.start_preview())
                    # Use the real allowlist for the explicit unknown context.
                    identity.side_effect = lambda record=None: record or {"driver_id":"other-user"}
                    session=manager.start_recording()
                    time.sleep(.15)
                    self.assertTrue(session["recording"])
                    self.assertIsNone(session["driver_id"])
                    self.assertTrue(channel.frames_written > 0)
                    manager.stop_recording()
                    context.assert_not_called()
                    writer.release.assert_called_once()
            finally:
                running.clear()
                thread.join(timeout=2)
                manager.release()
            self.assertFalse(channel.is_live)

    def test_ice_configuration_accepts_turn_and_rejects_invalid_data(self):
        servers=[{"urls":["turns:relay.example.test:443"],"username":"test","credential":"test"}]
        with patch.dict(os.environ, {"ROADWATCH_WEBRTC_ICE_SERVERS":json.dumps(servers)}):
            self.assertEqual(rtc_configuration()["iceServers"],servers)
        for value in ('{}','[]','[{"urls":"https://invalid"}]','bad'):
            with patch.dict(os.environ, {"ROADWATCH_WEBRTC_ICE_SERVERS":value}), self.assertRaises(ValueError):
                rtc_configuration()

    def test_browser_ai_never_uses_global_driver(self):
        manager=DualCameraManager.from_settings({"camera_input_mode":"browser"})
        manager.pipeline=Mock()
        frame=np.zeros((8,8,3),dtype=np.uint8)
        manager.pipeline.process.return_value=(frame,{"objects":[]})
        with patch("services.driver_monitoring.runtime.identity_metadata", return_value={"driver_id":"another-user"}):
            _,event=manager._handle_ai_frame("front",frame,1,time.time())
        self.assertIsNone(event["driver_id"])
        manager.release()


if __name__ == "__main__":
    unittest.main()
