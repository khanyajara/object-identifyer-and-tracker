# Driver fatigue: implementation and acceptance

Reverification on 2026-09-08: **106 tests passed**, compilation and scoped whitespace checks passed. A recording-boundary race was fixed by capturing the video context and time origin together; frames captured before a new recording cannot seed its fatigue windows. New regressions cover a recording stop during event generation and the complete synthetic frame → actual local JSON → existing incident-list path. Native preflight still fails readiness: YuNet loads and executes (latest blank-frame mean 36.238 ms), but SFace/landmarker assets, MediaPipe, camera selection and calibrated thresholds remain unavailable. Download retries timed out; pip additionally reported a temporary-file lock. Real-camera validation is not complete.

The three phases now have deployment checks/calibration tools, a temporal detector, and linked local events with a live warning. Real-camera acceptance is still pending. Default eye/mouth/neutral-pitch settings remain blank: an administrator must validate thresholds before alerts can operate. No normal-user setup controls were added.

## 1. Native validation and calibration

Run `python scripts/validate_driver_deployment.py --output docs/driver_deployment_validation.json` in the project virtual environment. It loads `.env`, checks native model loading, hashes available assets, and times YuNet on 20 blank frames after warm-up. It opens no camera and reads no biometric database. Missing prerequisites produce a nonzero exit code. Configured thresholds are not evidence of calibration accuracy.

This machine successfully downloaded the OpenCV YuNet 2023 model and loaded/executed it with OpenCV 4.13.0. The recorded blank-frame mean is about 39.5 ms, which is not a driver-camera benchmark. SFace and Google's landmark bundle downloads timed out. The pinned MediaPipe package download repeatedly timed out and was cancelled. The camera source and recognition/fatigue thresholds are unset. The existing `.env` also produces a parse warning at line 13; no credential values were printed or changed. The JSON report is the authoritative prerequisite snapshot.

Model sources: [OpenCV YuNet](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet), [OpenCV SFace](https://github.com/opencv/opencv_zoo/tree/main/models/face_recognition_sface), and [Google Face Landmarker](https://developers.google.com/edge/mediapipe/solutions/vision/face_landmarker). Use the 2023 YuNet asset compatible with the current OpenCV adapter. Model binaries remain ignored by Git.

`python scripts/calibrate_driver_fatigue.py samples.csv --output calibration.json` accepts scalar `label,value` rows. Labels are `eyes_closed`, `eyes_open`, `mouth_rest`, `yawn`, `neutral_pitch`; provide at least 20 samples per label from consented, stationary calibration. For eyes use the maximum of the two opening ratios. The helper finds a midpoint between the closed/rest 95th percentile and open/yawn 5th percentile; it rejects overlap and insufficient samples. These are suggestions only. It never edits deployment settings or stores images. Neutral pitch is the median; verify that positive relative pitch denotes downward movement with the mounted camera.

Validate suggestions on separate labelled data across drivers, glasses, lighting and head turns. `python scripts/evaluate_driver_fatigue.py validation.csv --output evaluation.json` replays strictly increasing `timestamp_seconds,left_eye,right_eye,expected_warning` rows; `expected_warning` is 0 or 1. Optional fields are `mouth,pitch,yaw,roll,face_visible,driver_id,video_id`. The output contains sample confusion counts, unavailable samples and meaningful-event count. It uses the same temporal engine and `.env` settings as deployment, saves no input measurements, and does not equate sample accuracy with driving safety validation.

For hardware acceptance, identify the existing rear cabin stream or dedicated camera index first. Start Roadwatch normally, enroll consented drivers, and verify recognition separately. With a stationary driver, test brief blinks, sustained eye closure, two yawns, two nods, occlusion, camera reconnection and a driver switch. Then compare CPU, RAM, capture FPS and inference timings with YOLO/OCR/encoding running; the existing Admin diagnostics exposes current timings and bounded queue counters. Native MediaPipe execution, recognition accuracy, threshold accuracy and simultaneous road-recording performance are not yet measured.

## 2. Temporal detection

`FatigueDetectionService` consumes the existing selected-driver landmark measurements inside the existing inference worker. It owns no camera, thread or neural model. Recognition still works if fatigue configuration is invalid. No history of raw landmarks or frames is persisted.

All windows use monotonic sample timestamps, not frame counts. Duplicate/backwards samples do not advance timers. Missing/invalid samples, samples older than 0.5 seconds, extreme yaw/roll, and gaps longer than 0.5 seconds break sustained closures/yawns/nods. Recording/identity context changes reset histories; a detected face-box jump also resets them. Snapshot freshness suppresses stale warnings when frames stop arriving.

Both eyes must be below the calibrated threshold. Brief closures of at least 0.1 seconds and shorter than 1.5 seconds count as blinks. The default prolonged-closure warning begins at 1.5 seconds and escalates at 3 seconds. These are configurable engineering defaults requiring field validation, not validated universal thresholds.

The `perclos` field is an EAR-threshold eye-closure fraction proxy: closed observed duration divided by valid observed duration in a 60-second window, available after 20 observed seconds. Missing time is excluded. It is not a calibrated measurement of 80% eyelid occlusion. Adjacent samples must both indicate closure to contribute closed duration. A fraction of 0.3 triggers a warning by default. Short blinks may be undersampled at the 10 FPS target rate.

Mouth opening held for 2 seconds counts once per yawn episode. Calibrated downward pitch held for 0.5 seconds counts once per nod episode; returning below threshold rearms it. Two yawns or two nods within the window contribute warnings. Missing mouth or neutral-pitch calibration disables that signal without disabling calibrated eye monitoring.

The transparent score is a heuristic severity index, not a probability: prolonged closure 60 (90 at critical duration), elevated closure fraction 60, repeated yawns 40, repeated nods 50; two or more reasons add 20 up to 100. Scores 40–79 are medium, 80+ high. Warning events have a 30-second cooldown; escalation to high can emit sooner, but momentary recovery cannot bypass the cooldown. All histories and pending queues have explicit capacity limits.

## 3. Alerts and linked evidence

The existing driver status shows “Possible fatigue detected. Stop safely and take a break.” while a fresh medium/high signal exists. It adds no page or controls and sends no email, webhook or other external notification.

Meaningful events capture event-time driver identity (including unknown), session, video, trip, vehicle, UTC timestamp, monotonic video offset, score, severity and aggregate reasons. A GPS fix is attached only when it belongs to the same video and precedes the event by at most 15 seconds. Missing/stale GPS stays null. No driver identity is inferred from a later recording or GPS fix.

Each event is written atomically under `data/fatigue_events/`, with an allowlist excluding images, embeddings and raw landmarks. A bounded 32-event pending queue retries storage failures after 30 seconds; overflow drops the oldest pending event. Recording is independent of storage failure. Diagnostics reports pending events and storage failure. Native/disk calls can delay the monitoring worker but do not run in the recorder or UI thread.

Events appear as `driver fatigue` in the existing incident list, with stable IDs and video offsets. Existing incident status edits survive regeneration. The linked video remains the evidence; no additional face snapshots or Supabase uploads are created. This implementation persists fatigue events locally; it does not add a new Firebase event-sync transport or automatic video clipping.

## Verification

Run `python -m unittest discover -s tests`. Tests cover temporal duration, bilateral closure, sparse/invalid/duplicate samples, context changes, coverage, yawns/nods, cooldown/escalation, bounded histories, calibration rejection, storage allowlists, GPS freshness, linked event creation, storage backoff and the existing UI warning.

Run `python scripts/stability_driver_monitoring.py --fatigue --seconds 180 --output docs/fatigue_stability.json` for an overloaded synthetic-camera run using the real worker and camera lifecycle. Camera/models/database and event persistence are mocked; this validates bounded orchestration, not native inference accuracy. The aggregate JSON records RAM, threads, CPU, queue depth, model initialization and event-write counts.

Validation on this change: 103 tests passed; Python compilation and application-scope whitespace checks passed. The 180.14-second fatigue stability run passed across 348 rerun simulations: queue peak 1, warm RSS growth 0.04 MiB, mean CPU 4.72% of one logical core, one camera open/release and one initialization of each synthetic model adapter. It dropped 290 old frames rather than accumulating latency, made one mock recognition-index read and one verification write, and emitted eight mock fatigue-event writes. The three monitoring/capture threads stopped; total process threads fell from baseline 22 to 16 as other existing threads also retired. Final recovery refinements were checked by the regression suite; the synthetic CPU result does not estimate native MediaPipe or concurrent YOLO performance.
