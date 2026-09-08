"""Thin Streamlit presentation. Model calls and enrollment run in the worker."""
import html
import streamlit as st
from .biometric_store import require_admin
from .runtime import get_runtime


def driver_status():
    """One read-only status; automatic startup belongs to the application lifecycle."""
    @st.fragment(run_every=1)
    def status():
        runtime = get_runtime()
        data = runtime.snapshot()
        fatigue = data.get("fatigue", {})
        if fatigue.get("severity") in {"medium", "high"}:
            st.warning("Possible fatigue detected. Stop safely and take a break.")
        elif data["identity_status"] == "verified":
            name = html.escape(data["driver_display_name"])
            st.markdown(f"**DRIVER** · {name} · Verified")
        elif not runtime.running or data.get("error") or data["message"].endswith("unavailable."):
            st.caption("DRIVER MONITORING · You may need to restart the application or check the camera and model configuration.")
        else:
            st.caption("DRIVER · Unknown")
    status()

def drivers_page(settings):
    actor = st.session_state.get("admin_account") if st.session_state.get("admin_authenticated") else None
    try:
        require_admin(actor)
    except PermissionError:
        st.error("Administrator access required.")
        return
    st.title("Drivers")
    try:
        runtime = get_runtime()
        drivers = runtime.service.store.list_drivers(actor)
    except Exception:
        st.info("Driver management unavailable. Configure Firebase Admin credentials and private biometric collection rules.")
        return
    if drivers:
        fields = ("driver_id", "display_name", "vehicle_id", "enrollment_status", "recognition_enabled", "last_recognized")
        st.dataframe([{key: row.get(key) for key in fields} for row in drivers], hide_index=True, width="stretch")
    with st.form("add-roadwatch-driver"):
        driver_id = st.text_input("Driver ID")
        name = st.text_input("Name")
        vehicle = st.text_input("Assigned vehicle")
        if st.form_submit_button("Add Driver"):
            try:
                runtime.service.store.add_driver(actor, driver_id, name, vehicle or None)
                st.rerun()
            except Exception:
                st.error("Could not add driver. Check the ID is unique and the private database is available.")
    with st.expander("Advanced Driver Monitoring Diagnostics"):
        st.json(runtime.diagnostics())
        if st.button("Refresh recognition index", disabled=not runtime.running):
            st.session_state.driver_action_future = runtime.manage_enrollment(lambda: None)
    if not drivers:
        return
    selected = st.selectbox("Driver", [item["driver_id"] for item in drivers])
    st.caption("Enrollment replaces only biometric data. Keep one driver in view with good lighting and slight natural head turns.")
    consent = st.checkbox("The driver agrees to biometric enrollment for Roadwatch driver recognition.")
    pending = st.session_state.get("driver_action_future")
    busy = (pending is not None and not pending.done()) or runtime.snapshot()["enrollment"]["status"] == "capturing"
    if st.button("Enroll Face / Re-enroll", disabled=not consent or busy or not runtime.running):
        import time
        st.session_state.driver_action_future = runtime.submit(lambda: runtime.enrollment.start(actor, selected, time.monotonic()))
    if st.button("Cancel enrollment", disabled=not runtime.running):
        st.session_state.driver_action_future = runtime.manage_enrollment(lambda: None)
    for label, action in (
        ("Disable Recognition", lambda: runtime.service.store.set_enabled(actor, selected, False)),
        ("Enable Recognition", lambda: runtime.service.store.set_enabled(actor, selected, True)),
        ("Delete biometric enrollment", lambda: runtime.service.store.delete_enrollment(actor, selected)),
    ):
        if st.button(label, disabled=pending is not None and not pending.done()):
            try:
                if runtime.running:
                    st.session_state.driver_action_future = runtime.manage_enrollment(action)
                else:
                    action()
                    runtime.service.sessions.invalidate()
                    runtime.service.index.invalidate()
                    st.success("Driver enrollment updated.")
                    st.rerun()
            except Exception:
                st.error("Driver update unavailable. Enrollment and private database access are required.")

    @st.fragment(run_every=1)
    def progress():
        future = st.session_state.get("driver_action_future")
        if future is not None and future.done():
            try:
                future.result()
            except Exception:
                st.error("Enrollment could not start. Check calibrated threshold and model configuration.")
            st.session_state.pop("driver_action_future", None)
        status = runtime.snapshot()["enrollment"]
        st.caption(f'{status["status"].title()}: {status["samples"]}/5 samples. {status["message"]}')
    progress()
