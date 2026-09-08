import unittest
from pathlib import Path
from uuid import uuid4
from streamlit.testing.v1 import AppTest


class DriverNavigationTests(unittest.TestCase):
    def app_from_script(self, script, default_timeout=40):
        # AppTest.from_string uses Windows TEMP, which may be outside the sandbox.
        directory = Path(__file__).resolve().parents[1] / "tmp"
        directory.mkdir(exist_ok=True)
        path = directory / ("driver_ui_" + uuid4().hex + ".py")
        path.write_text(script, encoding="utf-8")
        self.addCleanup(path.unlink, missing_ok=True)
        return AppTest.from_file(str(path), default_timeout=default_timeout)

    def test_public_and_admin_navigation_survive_reruns(self):
        script = '''
import streamlit as st
from unittest.mock import patch
import streamlit_app as app
from services.driver_monitoring.runtime import DriverMonitoringRuntime
from services.driver_monitoring.config import MonitoringConfig
runtime = st.session_state.get("test_runtime")
if runtime is None:
    runtime = DriverMonitoringRuntime(MonitoringConfig())
    st.session_state.test_runtime = runtime
with patch.object(app, "load_settings", return_value=dict(app.DEFAULTS)), \
     patch.object(app, "privacy_permission_gate", return_value=True), \
     patch.object(app, "ensure_location_tracking"), \
     patch.object(app, "ensure_background") as automatic_start, \
     patch.object(app, "upload_status_widget"), \
     patch.object(app, "sidebar_status"), \
     patch.object(app, "system_top_bar"), \
     patch.object(app, "roadwatch_home_page", side_effect=lambda *a: st.write("Public recorder")), \
     patch.object(app, "admin_dashboard_page", side_effect=lambda *a: st.write("Admin dashboard")), \
     patch("services.driver_monitoring.ui.get_runtime", return_value=runtime), \
     patch.object(runtime.service.store, "list_drivers", return_value=[]):
    app.main()
    assert automatic_start.call_count == 1
'''
        app=self.app_from_script(script).run()
        self.assertEqual(len(app.exception),0)
        self.assertNotIn("Drivers",app.sidebar.radio[0].options)
        app.session_state.admin_authenticated=True
        app.session_state.admin_account={"username":"test-admin","role":"admin"}
        app.run()
        self.assertEqual(len(app.exception),0)
        self.assertIn("Drivers",app.sidebar.radio[0].options)
        app.sidebar.radio[0].set_value("Drivers").run()
        self.assertEqual(len(app.exception),0)
        self.assertTrue(any(button.label=="Add Driver" for button in app.button))
        app.run()
        self.assertEqual(len(app.exception),0)

    def test_non_admin_cannot_render_driver_management(self):
        app=self.app_from_script('''
import streamlit as st
from services.driver_monitoring.ui import drivers_page
st.session_state.admin_authenticated=True
st.session_state.admin_account={"role":"viewer"}
drivers_page({})
''').run()
        self.assertEqual(len(app.exception),0)
        self.assertEqual(app.error[0].value,"Administrator access required.")
        self.assertEqual(len(app.text_input),0)

    def test_normal_driver_status_has_no_workflow_or_diagnostics(self):
        app=self.app_from_script('''
from unittest.mock import patch
from services.driver_monitoring.runtime import DriverMonitoringRuntime
from services.driver_monitoring.config import MonitoringConfig
from services.driver_monitoring.ui import driver_status
runtime=DriverMonitoringRuntime(MonitoringConfig(enabled=False))
with patch("services.driver_monitoring.ui.get_runtime",return_value=runtime):
    driver_status()
''').run()
        self.assertEqual(len(app.exception),0)
        self.assertEqual(len(app.button),0)
        self.assertEqual(len(app.selectbox),0)
        self.assertEqual(len(app.expander),0)
        self.assertEqual(len(app.caption),1)

    def test_fatigue_warning_uses_existing_status_without_controls(self):
        app=self.app_from_script('''
from unittest.mock import Mock, patch
from services.driver_monitoring.ui import driver_status
runtime=Mock()
runtime.snapshot.return_value={"fatigue":{"severity":"high"}}
with patch("services.driver_monitoring.ui.get_runtime", return_value=runtime):
    driver_status()
''').run()
        self.assertEqual(len(app.exception),0)
        self.assertEqual(len(app.warning),1)
        self.assertEqual(len(app.button),0)
