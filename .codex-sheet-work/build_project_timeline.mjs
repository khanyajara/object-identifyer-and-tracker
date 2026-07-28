import fs from "node:fs/promises";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const outputDir = "C:/Users/khany/OneDrive/Desktop/Personal-projects/Object-Detection-and-Tracking/outputs/project-timeline";
const outputPath = `${outputDir}/Roadwatch_Vision_Project_Timeline.xlsx`;
const workbook = Workbook.create();
const schedule = workbook.worksheets.add("Project Schedule");
const notes = workbook.worksheets.add("Planning Notes");

const phases = [
  [1, "Project foundation & initial prototype", "Set up object detection, tracking, video I/O, and the initial repository.", new Date(2025, 6, 19), new Date(2025, 7, 22), "Complete", "Historical milestone"],
  [2, "Core recorder & detection pipeline", "Build webcam/dashcam recording, YOLO detection, movement analysis, OCR, and saved-video workflow.", new Date(2025, 7, 23), new Date(2026, 4, 11), "Complete", "Historical milestone"],
  [3, "Dashboard & service integration", "Develop Streamlit dashboard plus incident, vehicle, reporting, storage, and notification services.", new Date(2026, 4, 12), new Date(2026, 5, 10), "Complete", "Historical milestone"],
  [4, "Video compatibility & GPS tracking", "Integrate MoviePy/FFmpeg, improve MP4/WebM handling, and enable active location tracking.", new Date(2026, 5, 11), new Date(2026, 6, 6), "Complete", "Historical milestone"],
  [5, "System & mobile testing", "Test vehicle workflows, webcam recorder reliability, mobile responsiveness, and media persistence.", new Date(2026, 6, 7), new Date(2026, 7, 7), "In Progress", "Current workstream"],
  [6, "Defect resolution & performance tuning", "Resolve test findings; tune recording stability, inference speed, playback, and location accuracy.", new Date(2026, 7, 8), new Date(2026, 7, 28), "Not Started", "Depends on Phase 5"],
  [7, "Feature hardening", "Finalize incidents, stolen-vehicle checks, analytics, exports, notifications, and error handling.", new Date(2026, 7, 29), new Date(2026, 8, 18), "Not Started", "Depends on Phase 6"],
  [8, "Security & data review", "Review authentication, secrets, access controls, retention, backup, and privacy settings.", new Date(2026, 8, 19), new Date(2026, 8, 30), "Not Started", "Depends on Phase 7"],
  [9, "User acceptance testing", "Run end-to-end tests using realistic driving scenarios and capture acceptance feedback.", new Date(2026, 8, 31), new Date(2026, 9, 25), "Not Started", "Depends on Phases 7–8"],
  [10, "Release preparation", "Write user/admin documentation, installation instructions, release notes, and deployment checklist.", new Date(2026, 9, 26), new Date(2026, 10, 15), "Not Started", "Depends on Phase 9"],
  [11, "Pilot deployment & monitoring", "Deploy a controlled pilot, monitor errors and usage, and apply priority fixes.", new Date(2026, 10, 16), new Date(2026, 11, 5), "Not Started", "Depends on Phase 10"],
  [12, "Production release & closeout", "Publish production release, hand over documentation, record lessons learned, and close the project.", new Date(2026, 11, 6), new Date(2026, 11, 18), "Not Started", "Target project completion"],
];

schedule.mergeCells("A1:H1");
schedule.getRange("A1").values = [["Roadwatch Vision Recorder — Project Timeline"]];
schedule.mergeCells("A2:H2");
schedule.getRange("A2").values = [["Draft schedule based on repository history; planning baseline: 14 July 2026"]];
schedule.getRange("A4:H4").values = [["Phase", "Milestone / Workstream", "Scope", "Start Date", "Target Completion", "Duration (days)", "Status", "Notes"]];
schedule.getRange("A5:H16").values = phases.map(([phase, name, scope, start, end, status, note]) => [phase, name, scope, start, end, null, status, note]);
schedule.getRange("F5").formulas = [["=E5-D5+1"]];
schedule.getRange("F5:F16").fillDown();
schedule.getRange("A18:E18").values = [["Project start", "Planning baseline", "Target completion", "Total timeline (days)", "Remaining from baseline (days)"]];
schedule.getRange("A19").formulas = [["=D5"]];
schedule.getRange("B19").values = [[new Date(2026, 6, 14)]];
schedule.getRange("C19").formulas = [["=E16"]];
schedule.getRange("D19").formulas = [["=C19-A19+1"]];
schedule.getRange("E19").formulas = [["=C19-B19"]];

schedule.getRange("A1:H1").format = { fill: "#123B5D", font: { bold: true, color: "#FFFFFF", size: 16 }, horizontalAlignment: "center", verticalAlignment: "center" };
schedule.getRange("A2:H2").format = { fill: "#DCEAF5", font: { color: "#274C6A", italic: true }, horizontalAlignment: "center" };
schedule.getRange("A4:H4").format = { fill: "#1F6D8A", font: { bold: true, color: "#FFFFFF" }, horizontalAlignment: "center", verticalAlignment: "center", wrapText: true };
schedule.getRange("A5:H16").format = { verticalAlignment: "center", wrapText: true };
schedule.getRange("A5:H16").format.borders = { preset: "inside", style: "thin", color: "#D8E1E8" };
schedule.getRange("A4:H16").format.borders = { preset: "outside", style: "thin", color: "#8AA7B8" };
schedule.getRange("A18:E18").format = { fill: "#E8F1E8", font: { bold: true, color: "#1D4E2A" }, horizontalAlignment: "center", wrapText: true };
schedule.getRange("A19:E19").format = { fill: "#F5FAF5", horizontalAlignment: "center" };
schedule.getRange("A18:E19").format.borders = { preset: "outside", style: "thin", color: "#A8C4AF" };
schedule.getRange("D5:E16").format.numberFormat = "yyyy-mm-dd";
schedule.getRange("A19:C19").format.numberFormat = "yyyy-mm-dd";
schedule.getRange("F5:F16").format.numberFormat = "#,##0";
schedule.getRange("D19:E19").format.numberFormat = "#,##0";
schedule.getRange("A5:A16").format.horizontalAlignment = "center";
schedule.getRange("D5:G16").format.horizontalAlignment = "center";
schedule.getRange("A1:H1").format.rowHeight = 28;
schedule.getRange("A2:H2").format.rowHeight = 22;
schedule.getRange("A4:H4").format.rowHeight = 34;
schedule.getRange("A5:H16").format.rowHeight = 43;
schedule.getRange("A:A").format.columnWidth = 9;
schedule.getRange("B:B").format.columnWidth = 31;
schedule.getRange("C:C").format.columnWidth = 50;
schedule.getRange("D:E").format.columnWidth = 16;
schedule.getRange("F:F").format.columnWidth = 15;
schedule.getRange("G:G").format.columnWidth = 15;
schedule.getRange("H:H").format.columnWidth = 24;
schedule.getRange("G5:G16").dataValidation = { rule: { type: "list", values: ["Not Started", "In Progress", "Complete", "At Risk"] } };
schedule.getRange("G5:G16").conditionalFormats.add("containsText", { text: "Complete", format: { fill: "#C6E0B4", font: { color: "#215E21" } } });
schedule.getRange("G5:G16").conditionalFormats.add("containsText", { text: "In Progress", format: { fill: "#FFE699", font: { color: "#7F6000", bold: true } } });
schedule.getRange("G5:G16").conditionalFormats.add("containsText", { text: "Not Started", format: { fill: "#E7EEF3", font: { color: "#485A66" } } });
schedule.getRange("G5:G16").conditionalFormats.add("containsText", { text: "At Risk", format: { fill: "#F4CCCC", font: { color: "#9C0006", bold: true } } });
schedule.freezePanes.freezeRows(4);
schedule.showGridLines = false;
schedule.tables.add("A4:H16", true, "ProjectScheduleTable");

notes.mergeCells("A1:E1");
notes.getRange("A1").values = [["Planning Notes & Assumptions"]];
notes.getRange("A3:B8").values = [
  ["Item", "Draft assumption"],
  ["Historical start", "19 July 2025, based on the earliest repository commit."],
  ["Planning baseline", "14 July 2026, the date this draft was prepared."],
  ["Current focus", "Vehicle/mobile testing, recorder reliability, media handling, and active location tracking."],
  ["Target completion", "18 December 2026, assuming planned testing and releases proceed without major scope changes."],
  ["How to use", "Update Status, dates, and notes as work progresses. Duration and summary dates update automatically."],
];
notes.getRange("A10:B13").values = [
  ["Status", "Meaning"],
  ["Not Started", "Scheduled work has not begun."],
  ["In Progress", "Active work is underway."],
  ["At Risk", "The target date may slip without corrective action."],
];
notes.getRange("A1:E1").format = { fill: "#123B5D", font: { bold: true, color: "#FFFFFF", size: 16 }, horizontalAlignment: "center" };
notes.getRange("A3:B3").format = { fill: "#1F6D8A", font: { bold: true, color: "#FFFFFF" }, horizontalAlignment: "center" };
notes.getRange("A10:B10").format = { fill: "#1F6D8A", font: { bold: true, color: "#FFFFFF" }, horizontalAlignment: "center" };
notes.getRange("A3:B8").format.borders = { preset: "all", style: "thin", color: "#D8E1E8" };
notes.getRange("A10:B13").format.borders = { preset: "all", style: "thin", color: "#D8E1E8" };
notes.getRange("A4:B8").format.wrapText = true;
notes.getRange("A:A").format.columnWidth = 22;
notes.getRange("B:B").format.columnWidth = 86;
notes.getRange("A3:B8").format.rowHeight = 33;
notes.getRange("A10:B13").format.rowHeight = 25;
notes.showGridLines = false;

const check = await workbook.inspect({ kind: "table", range: "Project Schedule!A1:H19", include: "values,formulas", tableMaxRows: 25, tableMaxCols: 10 });
console.log(check.ndjson);
const errors = await workbook.inspect({ kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A", options: { useRegex: true, maxResults: 100 }, summary: "formula error scan" });
console.log(errors.ndjson);
const preview = await workbook.render({ sheetName: "Project Schedule", range: "A1:H19", scale: 1.5, format: "png" });
await fs.mkdir(outputDir, { recursive: true });
await fs.writeFile(`${outputDir}/project_schedule_preview.png`, new Uint8Array(await preview.arrayBuffer()));
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
console.log(outputPath);
