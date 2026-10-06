# Debug analysis — 6 October 2026

This is the pre-repair diagnostic snapshot. The subsequent [phase repair report](phase_repair_report.md) describes the corrected implementation and latest verification; failures below are retained as historical evidence.

## Result

The project is partially working. Full pytest execution using the project virtual environment plus the existing user-installed pytest produced **181 passed, 14 failed, 2 collection errors**, and 9 passing subtests. Both collection errors originate from the missing `core.video_io.validate_video_file` function. All 14 failures are in `tests/test_detection_audit.py`. Syntax parsing passed for 107 Python files.

Application source was not modified. This report and diagnostic artifacts were added.

## What works

| Area | Evidence | Limit |
|---|---|---|
| YOLO object detection | Local `yolov8n.pt` detected a bus at 88.93% confidence and a person at 91.29% on bundled images. | These samples do not establish real-world accuracy. |
| Deep SORT | Repeated sample frames retained tracking ID 1; per-role sample trackers ran. | Shared-model concurrency safeguards failed a separate regression. |
| MP4 writing and compression | `mp4v` writer, MoviePy compression, 18,885-byte playable output. | Diagnostic copy reset movement between unrelated images and corrected an outdated finalizer call. The original smoke script fails. |
| Face model runtime | YuNet native load and inference, SFace load, and MediaPipe landmarker load passed. | Identity and fatigue readiness remain false. |
| Accounts, API authorization, camera/browser plumbing, cloud-service logic, driver services, missing-person persistence | Existing collectable tests passed outside the sandbox. | Many tests use mocks; this does not confirm live cameras or remote backends. |
| Python syntax | 107 files parsed without errors. | Syntax checks do not establish runtime correctness. |

## What fails or is incomplete

| Priority | Area | Finding |
|---|---|---|
| High | Upload module | `services/recording_upload_service.py:6` imports nonexistent `validate_video_file`. Import fails, blocking upload and ownership test modules. |
| High | Movement | `core/movement.py:15` compares frames without checking dimensions. Different resolutions cause an OpenCV exception. `VisionPipeline` also shares movement history across camera roles. |
| High | Ownership integration | Recording creation rejects the `user_id` argument expected by the regression; UI has no `account_videos` helper expected by the access regression. Ownership isolation is not established by this run. Missing interfaces alone do not prove unauthorized access. |
| High | Processing error handling | AI error events are not rejected as expected; capture is not released when writer setup fails. |
| Medium | OCR scheduling | Initial media timestamp 0 does not trigger OCR; timers are shared across feeds. Lazy pipeline does not pass the expected OCR reader. OCR backend loads, but plate recognition accuracy was not validated. |
| Medium | Inference handling | Internal `TypeError` is not handled as expected by the regression. Input validation and shared-model locking regressions also fail. |
| Medium | Preview | Expected smooth-preview method is absent, smooth-preview selection is ignored, and resolution-change overlay behavior cannot pass its regression. |
| Medium | Recording summaries | Expected distinct-track `object_counts` summary is absent. Same-second filename regression is blocked by missing `user_id` support; a collision was not independently reproduced. |
| Medium | Driver monitoring readiness | Recognition, eye, mouth, and neutral-pitch calibration thresholds are all absent. Models load but deployment preflight reports `ready_for_camera_acceptance=false`. |
| Low | Dependency/test setup | Virtual environment lacks pytest. Existing user-installed pytest was used without installing packages. `pip check` flags MediaPipe's missing `opencv-contrib-python` dependency; project instructions deliberately retain `opencv-python` to avoid conflicting cv2 wheels, and native model loading succeeds. |
| Low | Offline check script | It assigns the return of `finalize_video_file` to a path, but the function returns `None`. After movement isolation, the original script fails at compression for this reason. |
| Low | Documentation/configuration | README describes new WebM recordings, while `core/video_io.py` defaults to MP4. Settings contain trailing malformed characters in the Firebase client email; runtime credentials may override this, and no authenticated cloud request was made. |

## Not verified

Physical camera availability, simultaneous camera capture, sustained FPS, browser WebRTC transport, real licence plate accuracy, driver identity accuracy, fatigue accuracy, authenticated Supabase/Firebase uploads, GPS device behavior, and external notification delivery remain unverified. No private cloud records were queried and no notifications were sent.

## Evidence and reproduction

- `debug-pytest-results.log`: final pytest failures and collection errors.
- `docs/debug-driver-preflight.json`: native face-model and calibration checks.
- `docs/debug-offline-vision.json`: successful isolated detection/tracking/video results.
- `debug-vision-results.log`: original offline script movement crash.
- `scripts/debug_offline_isolated.py`: diagnostic-only copy with movement reset between test images and correct finalizer usage.

The first sandboxed unittest run had temporary-directory permission errors. Outside the sandbox, its collectable 181 unittest tests passed; three pytest-dependent modules could not import pytest in that runner. The subsequent pytest run exposed the genuine failures above. Temporary permission errors should not be counted as application defects.

Recommended repair order: restore video validation/imports; restore ownership interfaces and verify access behavior; isolate movement and OCR state per camera and reset on resolution changes; repair inference/processing cleanup; repair preview and summaries; calibrate driver thresholds and perform real-device/cloud acceptance checks.
