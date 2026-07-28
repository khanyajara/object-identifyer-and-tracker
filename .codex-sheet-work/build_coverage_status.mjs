import fs from "node:fs/promises";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const outputDir = "C:/Users/khany/OneDrive/Desktop/Personal-projects/Object-Detection-and-Tracking/outputs/project-coverage";
const outputPath = `${outputDir}/Roadwatch_Vision_Coverage_Status.xlsx`;
const wb = Workbook.create();
const overview = wb.worksheets.add("Overview");
const coverage = wb.worksheets.add("Coverage Register");
const history = wb.worksheets.add("Development History");

const implemented = [
  ["Live recording", "OpenCV webcam capture, in-page preview, start/stop controls, recording timer, storage status, and background capture thread.", "Implemented", "Field testing in progress", "streamlit_app.py; core/opencv_recorder.py"],
  ["Video files", "Original high-resolution WebM recordings, legacy MP4 compatibility, processed-video fallback, and local metadata per recording.", "Implemented", "Playback and compatibility testing in progress", "services/video_service.py; core/video_io.py"],
  ["AI object detection", "YOLOv8 detection for people, vehicles, and other COCO objects with configurable image size and confidence.", "Implemented", "Validate against real driving footage", "core/detector.py; core/vision_pipeline.py"],
  ["Object tracking", "Deep SORT tracking IDs and captured-object summaries for each recording.", "Implemented", "Validate tracking stability in vehicle tests", "core/tracker.py; core/vision_pipeline.py"],
  ["Movement analysis", "Movement detection and movement-event metadata captured against video IDs.", "Implemented", "Validate thresholds in varied conditions", "core/movement.py; services/detection_log_service.py"],
  ["OCR / plate scanning", "Optional EasyOCR plate scanning with plate text, confidence, and bounding-box metadata.", "Implemented", "Validate plate accuracy and OCR intervals", "core/ocr.py; core/vision_pipeline.py"],
  ["Video post-processing", "Full post-recording AI pass that produces annotated processed WebM video and rebuilds detection metadata.", "Implemented", "FFmpeg/MoviePy compatibility testing in progress", "services/video_processing_service.py"],
  ["Evidence library", "Search, filters, original/processed selector, video playback, detection timeline, object table, and missing-file diagnostics.", "Implemented", "Validate against a larger recording set", "streamlit_app.py"],
  ["Incidents", "Generated and manual incidents; filters by severity, type, and status; review and dismissal workflow.", "Implemented", "End-to-end workflow testing pending", "services/incident_service.py; streamlit_app.py"],
  ["Stolen vehicle watchlist", "Local reported-vehicle records, plate search, OCR matching, and high-severity incident creation for potential matches.", "Implemented", "Validate matching and report workflow", "services/stolen_vehicle_service.py"],
  ["GPS history", "Local/mock GPS history, live location-tracking service, speed/location status, and video-linked GPS points.", "Implemented", "Validate active GPS in vehicle/mobile tests", "services/gps_service.py; streamlit_app.py"],
  ["Emergency contacts & SOS", "Local contact management, active-contact count, SOS action, and notification dispatch integration.", "Implemented", "Test with configured notification provider", "services/contact_service.py; services/notification_service.py"],
  ["Vehicle profile", "Local vehicle and driver/fleet profile for reporting and future exports.", "Implemented", "Data-entry validation pending", "services/vehicle_profile_service.py"],
  ["Analytics & reports", "Aggregate recording, incident, detection, plate, movement, duration, and processing-error metrics with dashboard charts.", "Implemented", "Validate totals with representative data", "services/report_service.py; streamlit_app.py"],
  ["Settings & privacy", "Camera, AI, storage, sync/API, notification settings, environment loading, and privacy-consent gate.", "Implemented", "Security/configuration review pending", "streamlit_app.py; settings.json; .env.example"],
  ["Authentication", "Local admin authentication and development credential checks.", "Implemented", "Security review pending", "services/auth_service.py"],
  ["Cloud/sync connectors", "Optional Supabase upload/public URLs, Firebase document sync, upload queue, and external API sync service.", "Implemented (optional configuration)", "Requires service credentials and integration testing", "services/supabase_service.py; services/firebase_service.py; services/sync_service.py"],
  ["Optional API", "FastAPI application for future integration with an existing dashcam platform.", "Implemented", "Integration testing pending", "api_app.py"],
];

const historyRows = [
  [new Date(2025, 6, 19), "Project repository started", "Initial project files and documentation committed."],
  [new Date(2025, 7, 23), "Documentation update", "Repository documentation updated."],
  [new Date(2026, 4, 12), "Dashboard/service work added", "Current Streamlit application and supporting services added to repository history."],
  [new Date(2026, 5, 11), "Code preservation checkpoint", "Project code saved before further changes."],
  [new Date(2026, 5, 22), "Vehicle & mobile testing", "Vehicle testing and mobile testing work recorded."],
  [new Date(2026, 5, 24), "Contributor setup", "Project updated to make contributor onboarding easier."],
  [new Date(2026, 6, 1), "WebM & active location tracking", "MP4-to-WebM work and active location tracking recorded; testing identified as next step."],
  [new Date(2026, 6, 3), "Webcam recorder fixes", "Further recorder fixes recorded."],
  [new Date(2026, 6, 6), "MoviePy / FFmpeg work", "MoviePy integration and FFmpeg work recorded; saving information verified."],
];

overview.mergeCells("A1:F1");
overview.getRange("A1").values = [["Roadwatch Vision Recorder — Current Coverage Status"]];
overview.mergeCells("A2:F2");
overview.getRange("A2").values = [["Snapshot prepared 15 July 2026 from the current repository code, README, and commit history"]];
overview.getRange("A4:B4").values = [["Metric", "Current count"]];
overview.getRange("A5:B9").values = [
  ["Implemented workstreams", 18],
  ["Core AI capabilities", 4],
  ["Operational dashboard areas", 9],
  ["Optional integration connectors", 3],
  ["Latest recorded development activity", new Date(2026, 6, 6)],
];
overview.getRange("B9").format.numberFormat = "yyyy-mm-dd";
overview.getRange("D4:F4").values = [["Current position", "Meaning", "Priority"]];
overview.getRange("D5:F8").values = [
  ["Implemented in codebase", "Feature exists in the current repository.", "Maintain"],
  ["Field testing in progress", "Vehicle, mobile, recorder, media, and location behaviour require practical validation.", "High"],
  ["Integration testing pending", "Cloud services and optional API require configured credentials or external systems.", "Medium"],
  ["Security/configuration review", "Secrets, permissions, privacy settings, and deployment configuration require final review.", "High"],
];
overview.getRange("A11:F11").merge();
overview.getRange("A11").values = [["Use the Coverage Register as the detailed record of what has been covered. The verification column intentionally marks tests that are still needed; it does not mean the feature is absent."]];

coverage.mergeCells("A1:E1");
coverage.getRange("A1").values = [["Coverage Register — Implemented Work to Date"]];
coverage.mergeCells("A2:E2");
coverage.getRange("A2").values = [["Status reflects code and documentation evidence as at 15 July 2026"]];
coverage.getRange("A4:E4").values = [["Area", "Covered functionality", "Codebase status", "Verification / current state", "Primary evidence"]];
coverage.getRange("A5:E22").values = implemented;
coverage.tables.add("A4:E22", true, "CoverageRegisterTable");

history.mergeCells("A1:C1");
history.getRange("A1").values = [["Development History — Recorded Milestones"]];
history.mergeCells("A2:C2");
history.getRange("A2").values = [["Derived from repository commit dates and messages"]];
history.getRange("A4:C4").values = [["Date", "Milestone", "Recorded activity"]];
history.getRange("A5:C13").values = historyRows;
history.getRange("A5:A13").format.numberFormat = "yyyy-mm-dd";
history.tables.add("A4:C13", true, "DevelopmentHistoryTable");

for (const sheet of [overview, coverage, history]) {
  sheet.showGridLines = false;
  sheet.freezePanes.freezeRows(4);
}
for (const [sheet, titleRange, subtitleRange] of [[overview, "A1:F1", "A2:F2"], [coverage, "A1:E1", "A2:E2"], [history, "A1:C1", "A2:C2"]]) {
  sheet.getRange(titleRange).format = { fill: "#123B5D", font: { bold: true, color: "#FFFFFF", size: 16 }, horizontalAlignment: "center", verticalAlignment: "center" };
  sheet.getRange(subtitleRange).format = { fill: "#DCEAF5", font: { italic: true, color: "#274C6A" }, horizontalAlignment: "center" };
  sheet.getRange(titleRange).format.rowHeight = 28;
  sheet.getRange(subtitleRange).format.rowHeight = 22;
}
overview.getRange("A4:B4").format = { fill: "#1F6D8A", font: { bold: true, color: "#FFFFFF" }, horizontalAlignment: "center" };
overview.getRange("D4:F4").format = { fill: "#1F6D8A", font: { bold: true, color: "#FFFFFF" }, horizontalAlignment: "center" };
overview.getRange("A4:B9").format.borders = { preset: "all", style: "thin", color: "#D8E1E8" };
overview.getRange("D4:F8").format.borders = { preset: "all", style: "thin", color: "#D8E1E8" };
overview.getRange("A11:F11").format = { fill: "#FFF4CE", font: { color: "#7A5700" }, wrapText: true, verticalAlignment: "center" };
overview.getRange("A11:F11").format.rowHeight = 34;
overview.getRange("A:A").format.columnWidth = 31;
overview.getRange("B:B").format.columnWidth = 18;
overview.getRange("C:C").format.columnWidth = 5;
overview.getRange("D:D").format.columnWidth = 28;
overview.getRange("E:E").format.columnWidth = 50;
overview.getRange("F:F").format.columnWidth = 15;
overview.getRange("D5:D8").format.wrapText = true;
overview.getRange("E5:E8").format.wrapText = true;
overview.getRange("D5:D8").format.rowHeight = 35;

coverage.getRange("A4:E4").format = { fill: "#1F6D8A", font: { bold: true, color: "#FFFFFF" }, horizontalAlignment: "center", verticalAlignment: "center", wrapText: true };
coverage.getRange("A5:E22").format = { wrapText: true, verticalAlignment: "center" };
coverage.getRange("A4:E22").format.borders = { preset: "inside", style: "thin", color: "#D8E1E8" };
coverage.getRange("A:A").format.columnWidth = 26;
coverage.getRange("B:B").format.columnWidth = 68;
coverage.getRange("C:C").format.columnWidth = 27;
coverage.getRange("D:D").format.columnWidth = 39;
coverage.getRange("E:E").format.columnWidth = 47;
coverage.getRange("A4:E4").format.rowHeight = 34;
coverage.getRange("A5:E22").format.rowHeight = 42;
coverage.getRange("C5:C22").conditionalFormats.add("containsText", { text: "Implemented", format: { fill: "#C6E0B4", font: { color: "#215E21", bold: true } } });
coverage.getRange("D5:D22").conditionalFormats.add("containsText", { text: "pending", format: { fill: "#FFF2CC", font: { color: "#7F6000" } } });
coverage.getRange("D5:D22").conditionalFormats.add("containsText", { text: "in progress", format: { fill: "#FFE699", font: { color: "#7F6000" } } });

history.getRange("A4:C4").format = { fill: "#1F6D8A", font: { bold: true, color: "#FFFFFF" }, horizontalAlignment: "center" };
history.getRange("A4:C13").format.borders = { preset: "inside", style: "thin", color: "#D8E1E8" };
history.getRange("A5:C13").format = { wrapText: true, verticalAlignment: "center" };
history.getRange("A:A").format.columnWidth = 16;
history.getRange("B:B").format.columnWidth = 36;
history.getRange("C:C").format.columnWidth = 74;
history.getRange("A5:C13").format.rowHeight = 31;

const check = await wb.inspect({ kind: "table", range: "Coverage Register!A1:E22", include: "values,formulas", tableMaxRows: 24, tableMaxCols: 8 });
console.log(check.ndjson);
const errors = await wb.inspect({ kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A", options: { useRegex: true, maxResults: 100 }, summary: "formula error scan" });
console.log(errors.ndjson);
await fs.mkdir(outputDir, { recursive: true });
for (const [sheetName, fileName, range] of [["Overview", "overview_preview.png", "A1:F11"], ["Coverage Register", "coverage_preview.png", "A1:E22"], ["Development History", "history_preview.png", "A1:C13"]]) {
  const png = await wb.render({ sheetName, range, scale: 1.2, format: "png" });
  await fs.writeFile(`${outputDir}/${fileName}`, new Uint8Array(await png.arrayBuffer()));
}
const output = await SpreadsheetFile.exportXlsx(wb);
await output.save(outputPath);
console.log(outputPath);
