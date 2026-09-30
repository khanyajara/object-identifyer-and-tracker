# Roadwatch detection and application audit — 2026-09-30

## Outcome

The installed YOLOv8n, Deep SORT, EasyOCR, OpenCV and MoviePy path ran successfully offline. The full suite passed after the fixes: **228 tests, 9 subtests**, with one existing Starlette/httpx deprecation warning. This is a code and local-runtime audit, not a certification of road-scene accuracy, production security or live camera/network reliability.

At the user's request, minimum confidence is now **0.50**, including the shared policy, new-install defaults, settings slider and this machine's existing settings.json. Smooth preview is **off by default** and retained as an explicit option for future streaming. The normal preview displays detections on their actual inference frames. The UI layout and WebRTC configuration are unchanged.

## Confirmed findings and fixes

| Finding before this audit | Implemented change | Source |
| --- | --- | --- |
| Configured 0.90 confidence discarded all bus-image detections; the policy also prevented selecting below 0.88. | User-approved 0.50 floor/default and saved setting; stricter settings remain available. Confidence is not a measured accuracy percentage. | `core/detection_policy.py:5`; `streamlit_app.py:160`, `:2839`; `settings.json` |
| Smooth mode applied old boxes to newer camera frames and was the default. | Default off; exact-frame preview remains standard. Opt-in smooth mode drops boxes on absent frames, expired results or changed resolution. It remains an approximation, not motion-compensated streaming. | `streamlit_app.py:494`, `:1496`; `core/dual_camera.py:1273` |
| Cached YOLO instances share a mutable predictor; the installed Ultralytics `Model.predict` initializes it and replaces its arguments before calling inference. Per-session locks did not protect cross-session sharing. | Per-model lock covers prediction setup, inference and result extraction for every detector sharing that model. Independent models remain independent. | `core/detector.py:10`, `:43` |
| Passing None to YOLO can select built-in sample assets instead of reporting a bad camera frame. Crops could receive out-of-frame coordinates. | Reject empty/non-BGR inputs, clamp detection boxes to frame bounds, retain existing duplicate suppression. Missing weights produce an explicit error without an implicit download. | `core/detector.py:22`, `:38` |
| Front and rear cameras had independent trackers but shared movement history and OCR timing. Different resolutions could also break frame differencing. | Per-camera movement and OCR timers; OCR runs on the first eligible frame and when the timeline resets. Movement resets its baseline when frame dimensions change. | `core/vision_pipeline.py:25`, `:54`, `:67`; `core/movement.py:13` |
| Lazy initialization enabled OCR without supplying an OCR reader. | Load the configured optional reader during lazy initialization. Serialize use of the cached reader and log missing package/weights. Runtime OCR downloads are disabled; provision weights explicitly. | `core/lazy_vision_pipeline.py:15`, `:34`; `core/ocr.py:18` |
| Saved-video processing reused the live pipeline's tracker/OCR state and timeline. | Fork a fresh lazy pipeline for replay; share only the model through the protected cache, not tracking/movement/OCR clocks. | `core/lazy_vision_pipeline.py:22`; `streamlit_app.py:553` |
| Any TypeError from inference caused a second inference call under the assumption that it was an old signature. Combined preview events dropped error details. | Inspect supported keyword arguments before calling once; surface internal errors in the combined preview. | `core/dual_camera.py:1340`, `:1251`, `:1292` |
| Lazy initialization can return an error event, and dual post-processing treated such frames as success. A missing pipeline could also produce an unannotated success. | Fail processing on missing pipelines or error events. Keep the source, release capture/writer and prevent upload. Release capture if writer setup fails; label compression failures accurately. | `services/dual_video_processing.py:91`, `:149`, `:237` |
| Dual-camera summaries counted every frame's label rather than unique tracked objects and used a different metadata shape from single-camera summaries. | Produce the standard nested summary, deduplicate track counts, keep peak counts, unique plates and movement transitions. Existing saved summaries are not automatically rewritten. | `services/detection_log_service.py:93`; `services/dual_session_service.py:87` |
| Capture filenames used only seconds (and camera role), allowing simultaneous sessions for one account to overwrite one another. | Include the recording/session random ID suffix in capture filenames. Existing paths remain usable. | `services/video_service.py:57`; `core/dual_camera.py:1112` |
| The video library checked ownership, but an incident/report could invoke the generic player without that check. | Check account ownership before fetching a playback URL or rendering a video. Existing administrator access is preserved. | `streamlit_app.py:1251` |
| Deep SORT initialization failures silently activated fallback tracking. | Log the cause and explicitly retain the named IoU fallback backend. | `core/tracker.py:15` |

## Tools and actual runtime verification

Installed versions inspected locally: Ultralytics 8.4.62, OpenCV 4.13.0.92, deep-sort-realtime 1.3.2, Torch 2.12.0, MoviePy 2.2.1 and EasyOCR 1.7.2. Tests used the existing environment; no package was installed or upgraded.

The reproducible script `scripts/offline_vision_check.py` blocks socket connections and uses local model weights plus the two bundled Ultralytics image assets. It records machine-readable results in `docs/offline_vision_check.json`.

| Input | At 90% before tuning | At 50% after tuning |
| --- | --- | --- |
| bus.jpg | No accepted objects | 1 bus, 3 people |
| zidane.jpg | 1 person | 2 people |

At 50%, the bus scored approximately 88.93%; people in that image scored 87.46%, 80.85% and 50.37%. Repeating each image retained all returned track IDs. Separate image roles exercised separate camera state; the first movement result stayed false. This does not establish tracking quality under motion, occlusion or camera shake. EasyOCR initialized and ran on the vehicle crop; the samples are not a plate-recognition accuracy dataset.

The final smoke check additionally wrote six 640x480 annotated frames with OpenCV's mp4v writer, released it, finalized the exact returned file, compressed it with MoviePy, and successfully reopened the 19,657-byte output. Its temporary media were removed after the check. This exercised real local encoding/compression; cloud upload remains covered by fake-service tests.

## Test evidence

- Before this audit: full suite **214 passed, 9 subtests**.
- Initial focused detection/preview regressions: **30 passed, 2 subtests**.
- After all fixes and the approved confidence change: `python -m pytest tests/ -q` — **228 passed, 9 subtests**, one pre-existing Starlette/httpx warning.
- New tests in `tests/test_detection_audit.py` cover per-camera movement/OCR, timeline resets, resolution changes, reader initialization, fresh replay state, single-call failure handling, model locks, invalid inputs/missing assets, false-success rejection, optional smoothing, summary counts, filename collisions, ownership checks and capture cleanup.
- The existing detection-policy tests were updated for the user-approved 50% policy. Existing upload, account, API, browser-camera and driver-monitoring tests ran in the full suite.
- `git diff --check` passed. Runtime smoke produced pkg_resources and CPU pin_memory warnings; neither prevented inference or compression.

## Deployment and remaining limitations

1. **YOLO weights:** `yolov8n.pt` and `yolov8m.pt` exist locally but are not tracked in Git. A fresh deployment must provision the chosen weights at the configured model path. The new explicit missing-file error prevents a surprise network download or a false processed/uploaded success. This audit did not upload model assets or deploy the app.
2. **OCR:** EasyOCR 1.7.2 exists in the local venv but is absent from the current deployment requirements. Its two weight files are tracked even though the ignore patterns match future model files. Provision the optional OCR package and its compatible dependencies in the deployment environment if plate reading is needed; avoid installing conflicting OpenCV distributions. Without it, object detection can still work, OCR is disabled and a server warning is logged. No plate-specific detector was added: the existing heuristic reads text from the lower half of detected vehicles.
3. **Cloud services:** No credentials or live Supabase/Firestore operations were used. Storage policies, private bucket configuration, deployed model availability, Firestore rules/indexes, cloud restart behavior and signed-link access require deployment verification. The earlier pipeline/ownership changes remain in place and their tests pass. Video ownership is not a complete multi-tenant isolation audit of unrelated GPS, contacts, incidents or shared reporting data.
4. **WebRTC:** Component and ICE setup remain unchanged, as requested earlier. No browser was opened, and camera permissions, TURN reachability and sustained live streaming were not tested.
5. **Performance/accuracy:** CPU inference with OCR can be slower than the capture rate. Latest-frame queues and optional smoothing do not increase detector throughput. No representative road-video benchmark, false-positive rate, distance/night/weather evaluation, sustained FPS claim or road-safety guarantee is made. Lowering confidence admits additional uncertain detections, as requested.
6. **Model fallback:** Deep SORT worked in the local smoke check. Other deployments may fall back to IoU when optional embedder dependencies fail; the reason is now visible in logs. Cross-class NMS and the existing tracking algorithm were preserved.
7. **Historical data:** These changes apply to new processing/recordings. No unknown owners were inferred, historical metrics batch-rewritten, cloud objects migrated or missing September 10 videos recovered. See `recording_pipeline_repair.md` for the earlier evidence and safe recovery procedure.

## Files changed in this audit

`core/detection_policy.py`, `core/detector.py`, `core/tracker.py`, `core/ocr.py`, `core/movement.py`, `core/vision_pipeline.py`, `core/lazy_vision_pipeline.py`, `core/dual_camera.py`, `services/dual_video_processing.py`, `services/dual_session_service.py`, `services/detection_log_service.py`, `services/video_service.py`, `streamlit_app.py`, and the local `settings.json` confidence value. Added `tests/test_detection_audit.py`, updated `tests/test_object_detection_policy.py`, and added `scripts/offline_vision_check.py`, its JSON result and this report. Existing unrelated working-tree changes were retained.
