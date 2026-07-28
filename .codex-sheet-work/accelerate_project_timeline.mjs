import fs from "node:fs/promises";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const outputDir = "C:/Users/khany/OneDrive/Desktop/Personal-projects/Object-Detection-and-Tracking/outputs/project-timeline";
const outputPath = `${outputDir}/Roadwatch_Vision_Project_Timeline.xlsx`;
const input = await FileBlob.load(outputPath);
const workbook = await SpreadsheetFile.importXlsx(input);
const schedule = workbook.worksheets.getItem("Project Schedule");
const notes = workbook.worksheets.getItem("Planning Notes");

schedule.getRange("A2").values = [["Accelerated three-month schedule; planning baseline: 14 July 2026 | target completion: 14 October 2026"]];
const accelerated = [
  [5, "System & mobile testing", "Complete vehicle/mobile testing, webcam recorder reliability checks, and media persistence tests.", new Date(2026, 6, 7), new Date(2026, 6, 24), "In Progress", "Current workstream — finish by 24 Jul"],
  [6, "Defect resolution & performance tuning", "Resolve test findings; tune recording stability, inference speed, playback, and location accuracy.", new Date(2026, 6, 25), new Date(2026, 7, 7), "Not Started", "Depends on Phase 5"],
  [7, "Feature hardening", "Finalize critical incidents, stolen-vehicle checks, analytics, exports, and error handling.", new Date(2026, 7, 8), new Date(2026, 7, 21), "Not Started", "Critical features only"],
  [8, "Security & data review", "Review authentication, secrets, access controls, retention, backup, and privacy settings.", new Date(2026, 7, 22), new Date(2026, 7, 28), "Not Started", "Run in parallel where possible"],
  [9, "User acceptance testing", "Run end-to-end driving scenarios, capture acceptance feedback, and fix release-blocking issues.", new Date(2026, 7, 29), new Date(2026, 8, 11), "Not Started", "Depends on Phases 7–8"],
  [10, "Release preparation", "Complete essential documentation, installation steps, release notes, and deployment checklist.", new Date(2026, 8, 12), new Date(2026, 8, 23), "Not Started", "Keep documentation release-focused"],
  [11, "Pilot deployment & monitoring", "Deploy a controlled pilot, monitor priority errors, and apply only release-blocking fixes.", new Date(2026, 8, 24), new Date(2026, 9, 7), "Not Started", "Depends on Phase 10"],
  [12, "Production release & closeout", "Publish production release, hand over essential documentation, and close the project.", new Date(2026, 9, 8), new Date(2026, 9, 14), "Not Started", "Hard deadline: 14 Oct 2026"],
];
schedule.getRange("A9:H16").values = accelerated.map(([phase, name, scope, start, end, status, note]) => [phase, name, scope, start, end, null, status, note]);
schedule.getRange("F9").formulas = [["=E9-D9+1"]];
schedule.getRange("F9:F16").fillDown();
notes.getRange("B6").values = [["Vehicle/mobile testing, recorder reliability, media handling, and active location tracking. The remaining plan is compressed to three months."]];
notes.getRange("B7").values = [["14 October 2026, a fixed three-month deadline from the planning baseline. Scope must remain focused on release-critical work."]];
notes.getRange("B8").values = [["Update Status, dates, and notes weekly. Escalate any item marked At Risk immediately to protect the fixed completion date."]];

const check = await workbook.inspect({ kind: "table", range: "Project Schedule!A1:H19", include: "values,formulas", tableMaxRows: 22, tableMaxCols: 10 });
console.log(check.ndjson);
const errors = await workbook.inspect({ kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A", options: { useRegex: true, maxResults: 100 }, summary: "formula error scan" });
console.log(errors.ndjson);
const schedulePreview = await workbook.render({ sheetName: "Project Schedule", range: "A1:H19", scale: 1.5, format: "png" });
const notesPreview = await workbook.render({ sheetName: "Planning Notes", range: "A1:E13", scale: 1.5, format: "png" });
await fs.writeFile(`${outputDir}/project_schedule_preview.png`, new Uint8Array(await schedulePreview.arrayBuffer()));
await fs.writeFile(`${outputDir}/planning_notes_preview.png`, new Uint8Array(await notesPreview.arrayBuffer()));
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
console.log(outputPath);
