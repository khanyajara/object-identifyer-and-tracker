import streamlit as st

from services.missing_person_service import MissingPersonService, STATUSES


def missing_person_page(admin_mode=False):
    st.title("Missing Persons" if admin_mode else "Report Missing Person")
    st.caption("Submit a reference photo and last-seen details for manual case review.")
    service = MissingPersonService()
    if not admin_mode:
        with st.form("missing_person_report"):
            name = st.text_input("Full name")
            location = st.text_input("Last-seen location")
            seen_date = st.date_input("Last-seen date")
            seen_time = st.time_input("Last-seen time")
            contact = st.text_input("Contact number")
            reference = st.text_input("Case/reference number")
            description = st.text_area("Description, clothing and circumstances")
            photo = st.file_uploader("Reference photo", type=["jpg", "jpeg", "png", "webp"])
            submitted = st.form_submit_button("Submit report")
        if submitted:
            try:
                report = service.submit(dict(name=name, last_seen_location=location,
                    last_seen_datetime=f"{seen_date} {seen_time}", contact_number=contact,
                    case_reference=reference, description=description), photo.getvalue() if photo else None)
                st.success(f"Report submitted for review. Reference: {report['report_id']}")
            except ValueError as exc:
                st.error(str(exc))
        return
    if not st.session_state.get("admin_authenticated"):
        st.error("Administrator sign-in required.")
        return
    query = st.text_input("Search name, location or case reference").strip().casefold()
    status_filter = st.selectbox("Filter status", ["All", *STATUSES])
    reports = service.list_reports()
    for report in reports:
        if query and query not in " ".join(str(report.get(k, "")) for k in ("name", "last_seen_location", "case_reference")).casefold():
            continue
        if status_filter != "All" and report["status"] != status_filter:
            continue
        with st.expander(f"{report['name']} — {report['status']}"):
            st.image(report["photo_path"], width=240)
            for label, key in (("Last seen", "last_seen_datetime"), ("Location", "last_seen_location"), ("Contact", "contact_number"), ("Case reference", "case_reference"), ("Description", "description")):
                st.text(f"{label}: {report[key]}")
            status = st.selectbox("Review status", STATUSES, index=STATUSES.index(report["status"]), key=report["report_id"])
            if st.button("Save status", key="save_" + report["report_id"]):
                service.update_status(report["report_id"], status)
                st.rerun()
    if not reports:
        st.info("No missing-person reports yet.")
