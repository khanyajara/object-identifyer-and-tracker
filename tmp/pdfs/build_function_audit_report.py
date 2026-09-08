from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "output" / "pdf" / "Roadwatch_Vision_Function_Audit_Report.pdf"

NAVY = colors.HexColor("#12233F")
BLUE = colors.HexColor("#2563EB")
CYAN = colors.HexColor("#10B8D6")
GREEN = colors.HexColor("#138A5B")
AMBER = colors.HexColor("#B86B00")
RED = colors.HexColor("#B42318")
INK = colors.HexColor("#172033")
MUTED = colors.HexColor("#5B6577")
PALE = colors.HexColor("#F3F6FA")
LINE = colors.HexColor("#D7DEE8")


class AuditDoc(BaseDocTemplate):
    def __init__(self, filename):
        super().__init__(
            filename,
            pagesize=A4,
            leftMargin=18 * mm,
            rightMargin=18 * mm,
            topMargin=19 * mm,
            bottomMargin=17 * mm,
            title="Roadwatch Vision Function Audit Report",
            author="Codex",
            subject="Functional status, completed fixes, and recommended improvements",
        )
        frame = Frame(self.leftMargin, self.bottomMargin, self.width, self.height, id="body")
        self.addPageTemplates(PageTemplate(id="report", frames=[frame], onPage=self._page))

    def _page(self, canvas, doc):
        canvas.saveState()
        canvas.setFillColor(NAVY)
        canvas.rect(0, A4[1] - 10 * mm, A4[0], 10 * mm, stroke=0, fill=1)
        canvas.setFont("Helvetica-Bold", 8)
        canvas.setFillColor(colors.white)
        canvas.drawString(18 * mm, A4[1] - 6.5 * mm, "ROADWATCH VISION  |  FUNCTION AUDIT")
        canvas.setStrokeColor(LINE)
        canvas.line(18 * mm, 9 * mm, A4[0] - 18 * mm, 9 * mm)
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(MUTED)
        canvas.drawString(18 * mm, 5 * mm, "Object Detection and Tracking")
        canvas.drawRightString(A4[0] - 18 * mm, 5 * mm, f"Page {doc.page}")
        canvas.restoreState()


styles = getSampleStyleSheet()
styles.add(ParagraphStyle(name="TitleX", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=27, leading=31, textColor=NAVY, alignment=TA_LEFT, spaceAfter=8))
styles.add(ParagraphStyle(name="Subtitle", parent=styles["Normal"], fontSize=12, leading=18, textColor=MUTED, spaceAfter=16))
styles.add(ParagraphStyle(name="H1X", parent=styles["Heading1"], fontName="Helvetica-Bold", fontSize=17, leading=21, textColor=NAVY, spaceBefore=7, spaceAfter=9))
styles.add(ParagraphStyle(name="H2X", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=12, leading=15, textColor=BLUE, spaceBefore=6, spaceAfter=5))
styles.add(ParagraphStyle(name="BodyX", parent=styles["BodyText"], fontSize=9.2, leading=13.3, textColor=INK, spaceAfter=6))
styles.add(ParagraphStyle(name="Small", parent=styles["BodyText"], fontSize=7.7, leading=10.4, textColor=INK))
styles.add(ParagraphStyle(name="SmallHeader", parent=styles["BodyText"], fontName="Helvetica-Bold", fontSize=7.7, leading=10.4, textColor=colors.white))
styles.add(ParagraphStyle(name="SmallMuted", parent=styles["BodyText"], fontSize=7.5, leading=10, textColor=MUTED))
styles.add(ParagraphStyle(name="Kicker", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=8, leading=10, textColor=CYAN, spaceAfter=6))
styles.add(ParagraphStyle(name="Metric", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=18, leading=20, textColor=NAVY, alignment=TA_CENTER))
styles.add(ParagraphStyle(name="MetricLabel", parent=styles["Normal"], fontSize=7.5, leading=9, textColor=MUTED, alignment=TA_CENTER))


def P(text, style="BodyX"):
    return Paragraph(text, styles[style])


def table(rows, widths, header=True, font=7.7):
    converted = []
    for r, row in enumerate(rows):
        converted.append([P(str(cell), "SmallHeader" if header and r == 0 else "Small") for cell in row])
    t = Table(converted, colWidths=widths, repeatRows=1 if header else 0, hAlign="LEFT")
    commands = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.35, LINE),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS", (0, 1 if header else 0), (-1, -1), [colors.white, PALE]),
    ]
    if header:
        commands += [
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ]
    t.setStyle(TableStyle(commands))
    return t


def metric(value, label):
    box = Table([[P(value, "Metric")], [P(label, "MetricLabel")]], colWidths=[40 * mm], rowHeights=[12 * mm, 10 * mm])
    box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), PALE),
        ("BOX", (0, 0), (-1, -1), 0.6, LINE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return box


story = []
story += [
    Spacer(1, 10 * mm),
    P("TECHNICAL QUALITY REPORT", "Kicker"),
    P("Roadwatch Vision<br/>Function Audit", "TitleX"),
    P("What works, what was fixed, what was removed, and what should change next", "Subtitle"),
    Spacer(1, 4 * mm),
    Table([[metric("16 / 16", "Automated tests passing"), metric("0", "Known failing tests"), metric("2", "Duplicates removed"), metric("2", "Files changed")]], colWidths=[43 * mm] * 4, hAlign="LEFT", style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 3)])),
    Spacer(1, 9 * mm),
    P("Executive summary", "H1X"),
    P("The audited Python application compiles successfully and its full available automated test suite passes. One confirmed cloud-playback defect was repaired: the newer playback layer expected Supabase private-bucket and signed-URL support that the underlying service did not yet provide. Two identical helper implementations were also consolidated into shared utilities."),
    P("No tested function is currently known to be broken. The principal remaining risk is coverage: hardware capture, OCR, encoding, external notification delivery, and live cloud integrations cannot be fully proven by the existing unit tests."),
    Spacer(1, 4 * mm),
    P("Audit basis", "H2X"),
    table([
        ["Check", "Result", "Meaning"],
        ["Python compilation", "PASS", "Application, core, service, and test modules compile."],
        ["Automated tests", "PASS - 16 tests", "Authentication, API, storage, GPS, video metadata, cloud playback, and dual-camera AI behaviors passed."],
        ["Change validation", "PASS", "Updated files compile and the complete tests still pass."],
        ["Hardware/external systems", "PARTIAL", "Real cameras, codecs, OCR engines, SMTP/webhooks, Firebase, and Supabase require environment-specific integration tests."],
    ], [40 * mm, 35 * mm, 97 * mm]),
    PageBreak(),
]

story += [P("1. Functions and capabilities confirmed working", "H1X")]
working_rows = [
    ["Area", "Confirmed behavior", "Evidence"],
    ["Authentication", "Password hashing and verification; JWT issue/decode round-trip; admin authentication.", "Automated tests"],
    ["API security", "JWT required on protected endpoints; role checks enforced; sync endpoint authorization.", "Automated tests"],
    ["Video access safety", "Video IDs cannot escape the configured metadata directory.", "Automated tests"],
    ["GPS", "Browser GPS rejects invalid latitude and longitude values.", "Automated tests"],
    ["Local JSON storage", "Missing data returns an independent default value rather than shared mutable state.", "Automated tests"],
    ["Settings", "Credential-like values are not persisted into the local settings file.", "Automated tests"],
    ["Storage cleanup", "Original and processed evidence are excluded from cache-removal candidates.", "Automated tests"],
    ["Export cleanup", "Only failed temporary export artifacts are removed.", "Automated tests"],
    ["Upload validation", "Cloud upload accepts only an existing, compressed MP4 playback artifact.", "Automated tests"],
    ["Video metadata", "Repair logic restores MP4 playback fields from available metadata.", "Automated tests"],
    ["Cloud normalization", "Stable Supabase identifiers, MIME type, byte size, and cloud readiness are normalized.", "Automated tests"],
    ["Cloud playback", "Valid cached HTTPS URLs are reused; expired URLs are refreshed; local paths are rejected.", "Automated tests"],
    ["Private Supabase playback", "Signed-URL endpoint is used for private buckets.", "Automated tests"],
    ["Dual-camera AI", "AI frames are passed through the configured vision pipeline.", "Automated tests"],
]
story += [table(working_rows, [35 * mm, 105 * mm, 32 * mm]), Spacer(1, 5 * mm)]
story += [P("Statically healthy modules", "H2X"), P("The following modules compile cleanly and remain available to the application. Compilation confirms syntax and import structure, but should not be mistaken for complete runtime coverage."), table([
    ["Module group", "Functions and responsibilities"],
    ["core.annotation / detector / tracker / movement / ocr", "Frame annotation, YOLO loading/detection, object tracking, movement detection, and OCR scanning."],
    ["core.video_io / vision_pipeline", "Writer selection, video conversion/compression, playback selection, cleanup, and AI pipeline orchestration."],
    ["core.dual_camera / opencv_recorder", "Camera configuration, capture, previews, recording, AI sampling, health, session summaries, and camera probing."],
    ["services.video_* / compression / detection_log", "Recording metadata, processing, compression, thumbnails, and detection persistence."],
    ["services.incident / stolen_vehicle / vehicle_profile / contact", "Incident lifecycle, stolen-vehicle reports, vehicle profile, and emergency contacts."],
    ["services.firebase / supabase / cloud_playback", "Cloud metadata, storage upload/listing, playback URL creation, and normalization."],
    ["services.gps / notification / sync / upload_queue", "Location tracking, notifications, remote sync, and upload task state."],
    ["api_app / streamlit_app", "REST endpoints, authorization dependencies, pages, dashboards, playback, and administrative UI."],
], [55 * mm, 117 * mm])]

story += [PageBreak(), P("2. Changes completed during the audit", "H1X")]
story += [table([
    ["Change", "Previous condition", "Completed result"],
    ["Supabase constructor contract", "Cloud playback passed bucket privacy and expiry arguments that SupabaseService did not accept.", "Constructor now supports bucket_public and signed_url_expiry_seconds while preserving older callers."],
    ["Signed playback URLs", "SupabaseService had no create_playback_url method.", "Public buckets return a public URL; private buckets POST to the signed-object endpoint with a bounded TTL."],
    ["Playback validation", "No service-level validation for empty object paths or invalid expiry values.", "Missing paths fail clearly; expiry input is normalized to a positive integer."],
    ["Refresh behavior", "refresh_video_url always returned a public URL.", "Refresh now uses the correct public or signed playback path and includes timestamps."],
    ["Duplicate format_duration", "A helper was repeated in streamlit_app.py and services/report_service.py.", "Streamlit imports the shared report utility; duplicate removed."],
    ["Duplicate is_remote_video_source", "A helper was repeated in streamlit_app.py and core/video_io.py.", "Streamlit imports the shared video utility; duplicate removed."],
], [42 * mm, 63 * mm, 67 * mm])]
story += [Spacer(1, 6 * mm), P("Files changed", "H2X"), table([
    ["File", "Purpose"],
    ["services/supabase_service.py", "Private/public playback support, signed URL generation, expiry metadata, and validation."],
    ["streamlit_app.py", "Removed duplicate helpers and imported canonical implementations."],
], [62 * mm, 110 * mm])]

story += [Spacer(1, 7 * mm), P("3. Changes still recommended", "H1X")]
improvements = [
    ["Priority", "Change", "Reason / acceptance condition"],
    ["P1", "Add tests for camera and video failure paths", "Mock camera-open failure, writer/codec failure, dropped frames, thread shutdown, partial files, and processing exceptions."],
    ["P1", "Add cloud integration tests", "Verify upload, object listing, signed URL expiry, Firebase write/read, pagination, and retry behavior against non-production test projects."],
    ["P1", "Expose cloud privacy settings consistently", "Add bucket-public and signed-URL-expiry settings to the application configuration and pass them through every SupabaseService construction site."],
    ["P2", "Split streamlit_app.py", "The file exceeds 3,000 lines. Move pages, playback helpers, settings, and admin flows into focused modules without changing behavior."],
    ["P2", "Add pytest, coverage, and Ruff", "Standardize test execution, measure untested branches, and catch unused imports, unreachable code, and style defects automatically."],
    ["P2", "Test notifications and GPS background service", "Cover SMTP/webhook failure, timeouts, stop events, device errors, reverse-geocoding failure, and status transitions."],
    ["P2", "Resolve the Starlette/httpx deprecation", "Update compatible dependency versions so API tests run without the TestClient deprecation warning."],
    ["P3", "Document compatibility wrappers", "Mark intentionally retained aliases such as old writer entry points and define when they may be removed."],
]
story += [table(improvements, [18 * mm, 58 * mm, 96 * mm])]

story += [PageBreak(), P("4. Functions requiring repair or removal", "H1X")]
story += [P("Known broken functions", "H2X"), P("None remain among the behaviors covered by the current automated tests. The previously failing private-bucket playback path was repaired and its test now passes.")]
story += [P("Removed as redundant", "H2X"), table([
    ["Removed implementation", "Canonical implementation retained", "Why removal was safe"],
    ["streamlit_app.format_duration", "services.report_service.format_duration", "Identical formatting behavior; only the import location changed."],
    ["streamlit_app.is_remote_video_source", "core.video_io.is_remote_video_source", "Identical HTTP/HTTPS check; all Streamlit call sites use the imported helper."],
], [54 * mm, 60 * mm, 58 * mm])]
story += [Spacer(1, 6 * mm), P("Intentionally retained", "H2X"), table([
    ["Function family", "Decision"],
    ["Video writer helpers", "Retained because general, MP4-specific, WebM-compatibility, and dual-camera writers have different return contracts and failure behavior."],
    ["Firebase convenience wrappers", "Retained as a module-level public API over FirebaseVideoService."],
    ["Cloud playback convenience wrappers", "Retained for environment-driven callers that do not construct service objects directly."],
    ["utc_now helpers", "Retained within separate service modules to avoid unnecessary coupling; they are small and context-local."],
], [58 * mm, 114 * mm])]
story += [Spacer(1, 8 * mm), P("5. Verification and limitations", "H1X"), table([
    ["Verification", "Outcome"],
    ["unittest discovery", "16 tests executed; all passed."],
    ["compileall", "Application Python sources compiled without syntax failures."],
    ["Change regression check", "Full test suite remained green after both fixes and consolidation."],
    ["Known warning", "FastAPI TestClient emits a Starlette/httpx compatibility deprecation warning."],
    ["Tooling gap", "pytest, coverage, and pyflakes/Ruff are not installed in the project virtual environment."],
], [58 * mm, 114 * mm])]
story += [Spacer(1, 5 * mm), P("Scope note", "H2X"), P("A passing unit test proves the tested behavior under its controlled inputs. It does not prove physical camera availability, local codec support, OCR model accuracy, network connectivity, cloud permissions, SMTP delivery, or webhook availability. Those items should be validated in the deployment environment using the recommended integration tests.")]

story += [Spacer(1, 7 * mm), KeepTogether([P("Recommended next milestone", "H2X"), P("Implement the P1 test and configuration items first. The target exit criteria should be: repeatable camera failure simulations, non-production cloud round-trips, and one consistent bucket-privacy configuration used by every playback and upload path.")])]

doc = AuditDoc(str(OUTPUT))
doc.build(story)
print(OUTPUT)
