# Roadwatch driver monitoring

## Audit and implementation boundary

The pre-edit audit found:

- `core/opencv_recorder.py`: independent capture, sampling, vision and logging threads; a bounded AI queue; stop/finalize/compress lifecycle.
- `core/dual_camera.py`: independent camera channels, latest-frame access, per-camera AI queues and recording sessions. Rear means rear/cabin in existing settings; it is **not assumed to face the driver**.
- `core/vision_pipeline.py`: existing YOLO, Deep SORT and OCR processing. No modifications to those models or their scheduling.
- `services/video_service.py`, `dual_session_service.py`, `video_processing_service.py`, `dual_video_processing.py`: recording metadata and offline detection replacement.
- `firebase_service.py`: Admin SDK, REST and public-push metadata transports. Biometrics must never use the latter two.
- `supabase_service.py` and the existing uncommitted cloud playback services: preserved.
- `incident_service.py` and `gps_service.py`: existing video-linked events and location acquisition; no trip model exists yet.
- `vehicle_profile_service.py`: free-text driver name and vehicle registration, no enrolled-driver database. Authentication uses existing bcrypt-backed admin accounts and roles.
- `streamlit_app.py`: session-state camera manager, fragment refresh and separate public/admin navigation.
- Existing versions: OpenCV 4.13.0.92, NumPy 2.4.6, Streamlit 1.58.0, Ultralytics 8.4.62, Deep SORT 1.3.2, Firebase Admin 7.5.0. Declared deployment Python 3.12; the local virtual environment is Python 3.14.3.
- Existing YOLO/OCR weights; no YuNet, SFace, MediaPipe, face/fatigue subsystem or embedding-encryption utility.

The implementation plan was to create isolated monitoring/model/UI/test files, add optional metadata at existing boundaries, and leave existing recording, GPS, cloud and road-AI interfaces compatible. Performance conflicts identified: CPU/native inference thread contention, USB camera ownership, duplicate OpenCV wheels, cloud reads in recognition, and repeated initialization on Streamlit reruns. No unrelated working modules were rewritten. Existing user changes in Streamlit, Supabase, cloud files and generated data were preserved.

## Architecture and lifecycle

After Roadwatch's existing privacy gate, application startup calls the process-level singleton automatically. A server-configured cabin source starts without a user button. Dedicated capture reuses `CameraChannel`; a configured cabin/rear channel is shared with `DualCameraManager`, including adopting an already-running recorder channel. Starting a recording attaches its writer to that same capture thread. Stopping a recording detaches the writer while background capture continues. No road-facing camera is implicitly passed to recognition.

The frame producer uses a capacity-one queue: it replaces the old pending frame when inference falls behind. Frames older than one second (or the face-loss timeout if shorter), duplicate capture tokens and obsolete source generations are discarded. The command queue is bounded to four; oversized frames are reduced before inference. The producer, inference worker and existing camera capture are separate threads with stop events. Normal UI rendering reads the published state only. Camera/worker errors are logged once per failure episode and recovered with a controlled backoff; models are not reconstructed during recovery. Shutdown joins workers and releases capture and MediaPipe resources. Uninterruptible native calls may delay shutdown; this is reported rather than starting replacement threads on top of them.

The scheduler keeps an enabled flag, priority, target interval and last inference timestamp for each task. Detection runs first (priority 100), recognition next (80), and landmarks last (50). No face: 4 FPS detection, dropping to 1 FPS after 30 seconds. Face present during recording/trip: 5 FPS. Unverified identity: 2-second recognition checks; candidates or disputed identities: 1 second; verified: 7 seconds, reduced to 10 seconds while idle. Landmark extraction targets 10 FPS only with a face and active recording/trip, unless an administrator enables idle landmarks. Actual rates are bounded by fresh frames and inference latency. Model adapters initialize lazily once, including failed load attempts. Restart after changing model paths or installing weights. The scheduler is reusable for future tasks without adding a camera, detector or giant inference loop; road YOLO/tracking/OCR scheduling remains untouched.

`DriverMonitoringService.process_frame(frame)` is a synchronous API for **worker use**, not Streamlit callbacks. The UI reads `DriverMonitoringRuntime.snapshot()`. Streamlit reruns share one device-local runtime without reloading models or starting more workers. This deployment assumes one recorder device per Python process; a hosted multi-device service needs an explicit per-device registry and remote camera transport.

YuNet selects within a normalized driver-seat region using position, area and confidence. Similar competing selections are rejected. A large box jump resets an unverified candidate and accelerates revalidation of a verified identity. SFace uses OpenCV alignment and a normalized 128-dimensional embedding, compares only enabled registered profiles, and requires both a calibrated cosine threshold and a 0.05 best-versus-runner-up margin. The margin, seat region, quality gates and temporal behavior also need validation on the deployment population. A nearest match below the requirements never leaves the private recognition layer.

Three distinct, spaced, consistent matches are required by default. Candidates publish `driver_id=null` and `Unknown Driver`. Verified sessions include `verified_at` and remain cached through brief occlusion or one mismatch. Repeated disagreement with the current driver revokes identity; alternating unknown/other-driver results also count as disagreement. Revalidation temporarily speeds up while disputed. Five seconds without a face expires the session even if no new frame arrives. Camera resets, worker failures, model/database failure and local admin disable/removal revoke identity immediately. A returning driver after expiry needs fresh confirmation and receives a new session ID. Cache grace can retain the prior identity briefly while a different face is being confirmed; validate this behavior on the target camera.

Recognition failures, unconfigured thresholds, unavailable databases and missing models do not prevent recording, GPS or incident creation. Landmarks continue when recognition is disabled. Landmark failure does not prevent identity confirmation.

## Models and optional dependencies

Place these **binary model files**, not Git LFS pointer text, in `models/`, or override their paths with the environment variables below:

| Local name | Official source |
|---|---|
| `face_detection_yunet.onnx` | [OpenCV Zoo YuNet](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet), `face_detection_yunet_2023mar.onnx` |
| `face_recognition_sface.onnx` | [OpenCV Zoo SFace](https://github.com/opencv/opencv_zoo/tree/main/models/face_recognition_sface), `face_recognition_sface_2021dec.onnx` |
| `face_landmarker.task` | [MediaPipe Face Landmarker models](https://ai.google.dev/edge/mediapipe/solutions/vision/face_landmarker#models), Face Landmarker task bundle |

Review upstream licenses, record model checksums and freeze the exact files used for threshold calibration. Model weights are ignored by Git and never downloaded at app startup.

The base requirements are unchanged. Install the optional packages after the base environment:

```sh
python -m pip install -r requirements.txt
python -m pip install --no-deps -r requirements-driver-monitoring.txt
```

The add-on pins MediaPipe 1.0.1, absl-py 2.5.0, flatbuffers 25.12.19 and sounddevice 0.5.6. NumPy, certifi, matplotlib and cffi must already be present through the base environment. MediaPipe's metadata requests `opencv-contrib-python`; a regular install would add a conflicting `cv2` distribution (the resolver selected OpenCV 5.0.0.93 locally). This explicit `--no-deps` workflow reuses the existing OpenCV 4.13 stack. `pip check` will report that intentionally unsatisfied contrib distribution requirement. Validate the optional native runtime on both target platforms before rollout; no TensorFlow/PyTorch dependency is added for faces. Existing YOLO's PyTorch remains unchanged.

Local installation was attempted, but the MediaPipe wheel transfer repeatedly timed out and was stopped before installation. Native model smoke tests are therefore still required; mocked adapter tests do not establish binary/runtime compatibility.

## Configuration

All configuration is also documented in `.env.example`; no credentials were added there.

| Variable | Default / meaning |
|---|---|
| `DRIVER_MONITORING_ENABLED`, `DRIVER_MONITORING_BACKGROUND` | `true`, `true`; automatically starts the provisioned cabin source |
| `DRIVER_FACE_RECOGNITION_ENABLED` | `true`; landmarks work when false |
| `DRIVER_CAMERA_SOURCE` | `dedicated`; use `rear` only for a physically cabin-facing rear channel |
| `DRIVER_CAMERA_INDEX` | blank; required for dedicated capture, cannot collide with road cameras |
| `DRIVER_FRAME_WIDTH`, `DRIVER_FRAME_HEIGHT` | `640`, `480` |
| `DRIVER_FACE_DETECTION_FPS`, `DRIVER_LANDMARK_FPS` | `5`, `10` |
| `DRIVER_NO_FACE_FPS`, `DRIVER_IDLE_DETECTION_FPS`, `DRIVER_IDLE_AFTER_SECONDS` | `4`, `1`, `30` |
| `DRIVER_LANDMARKS_ENABLED`, `DRIVER_LANDMARKS_WHEN_IDLE` | `true`, `false` |
| `FACE_RECOGNITION_UNVERIFIED_INTERVAL_SECONDS` | `2`; legacy `FACE_RECOGNITION_INTERVAL_SECONDS` still accepted |
| `FACE_RECOGNITION_CANDIDATE_INTERVAL_SECONDS` | `1` |
| `FACE_RECOGNITION_VERIFIED_INTERVAL_SECONDS` | `7`; legacy `DRIVER_IDENTITY_RECHECK_SECONDS` still accepted |
| `DRIVER_EMBEDDING_INDEX_TTL_SECONDS` | `300` |
| `DRIVER_DATABASE_RETRY_SECONDS`, `DRIVER_RECOVERY_SECONDS` | `30`, `5` |
| `DRIVER_FACE_LOST_TIMEOUT_SECONDS` | `5` |
| `FACE_RECOGNITION_REQUIRED_MATCHES` | `3`; cannot be less than 2 |
| `FACE_RECOGNITION_THRESHOLD` | blank; recognition and enrollment remain disabled until calibrated |
| `DRIVER_SEAT_X`, `DRIVER_SEAT_Y`, `DRIVER_SEAT_RADIUS` | `0.5`, `0.5`, `0.35`; normalized image-space seat selection |
| `DRIVER_YUNET_MODEL`, `DRIVER_SFACE_MODEL`, `DRIVER_LANDMARK_MODEL` | paths listed above, relative to project root or absolute |
| `DRIVER_BIOMETRICS_RULES_CONFIGURED` | `false`; enable only after deploying restrictive collection rules |

There is deliberately no universal recognition threshold. Use consented held-out genuine and impostor samples, including glasses, different lighting and poses, to measure false accepts/rejects for the selected model/camera. Choose a threshold that meets the deployment's acceptance criteria, then test temporal confirmation and ambiguity handling separately. Enrollment uses that same threshold to reject inconsistent samples; calibration must precede live enrollment.

Provision a dedicated driver-camera index or explicitly identify the rear channel as cabin-facing in server configuration. This is an administrative deployment step, not a normal-user workflow. Never guess which physical camera faces the driver. If provisioned, the background stream starts automatically before recording and resumes after temporary device failure. Recognition can therefore be verified before the record button is pressed. If configuration/models/threshold are unavailable, the user sees only Unavailable or Unknown and records normally.

Normal users see one small read-only Driver status on the existing recorder page. Start/stop recognition buttons, camera configuration, similarity values, eye/head details and technical cards were removed. Admin retains profiles, enrollment/re-enrollment, enable/disable and removal. Only the optional Advanced Driver Monitoring Diagnostics expander shows scheduler, bounded-queue and aggregate counters, with an explicit index-refresh action.

## Enrollment and storage

Log in with an existing `admin` or `super_admin` account, open **Admin → Drivers**, add an ID/name/vehicle and enroll using the automatically running cabin source. Viewer/operator roles cannot access the management service. Confirm driver consent, then capture at least five fresh, well-lit, sufficiently large, sharp samples with slight pose variation. YuNet's eye/nose/mouth points must exist, and only one face may be present. Repeated identical frames and mutually inconsistent embeddings are rejected. Enrollment expires after 60 seconds. Cancellation or admin enable/disable/delete operations discard pending samples. Raw images are never written to disk; temporary vectors are released on completion/cancellation.

- `drivers/{driver_id}`: ordinary allowlisted driver metadata, enrollment status and last-recognized time.
- `driver_biometrics/{driver_id}`: embedding, model/version and update time; **server Admin SDK only**.
- Writes pair biometric and public enrollment updates in a Firestore batch. Removing enrollment does not delete driver metadata, videos, incidents or GPS history.
- No biometric local fallback, public push URL, REST fallback, normal video/API payload, log output, UI table or diagnostic array.
- Deploy the matches in `driver_monitoring_firestore.rules` into the existing rules. Remove/exclude overlapping broad allows; a deny match does not override another allow. Restrict service-account IAM and keep credentials server-side. The environment acknowledgement is a setup guard, not a substitute for deployed rules.
- Native Firestore encryption/access controls are used; no homemade cryptography was added.
- The private DriverEmbeddingIndex caches enrolled profiles in memory. Recognition comparisons make no Firestore call until a local enrollment revision, explicit invalidation or the five-minute TTL requires refresh. Local enroll/re-enroll/enable/disable/removal invalidates the index; local admin actions also revoke the active session immediately. Remote changes are observed after TTL refresh, so lower the TTL if faster cross-process revocation is required. Failed refresh discards stale vectors, masks recognition and backs off for 30 seconds. No-face operation does not load the index. Last-recognized writes occur on verification transitions, not revalidation frames. There are no continuous recognition uploads to Supabase.

## Identity, recordings and fatigue API

Public results expose `face_detected`, `identity_status`, confirmed `driver_id`/display name, `identity_confidence` (cosine similarity, not a probability), `landmarks_available`, measurements and `last_identity_check`. No candidate name is displayed. States are `no_face`, `detecting`, `candidate`, `verified`, `unknown`, `lost`.

The independent session includes its ID, driver/status, start/verified/last-seen timestamps, vehicle, video and optional trip ID. Existing vehicle registration is used as vehicle context when there is no explicit vehicle ID. Existing GPS video IDs remain the location join; optional trip IDs can be supplied later without creating a duplicate GPS/trip system.

Video start metadata and Firebase add optional `driver_id`, `driver_identity_status`, `driver_session_id`. They describe **recording start** and are not retroactively overwritten if another driver appears. Live road events carry their sampled identity; generated incidents prefer event identity over start metadata. GPS points carry current identity alongside their existing video link. Manual incidents use the selected video's context, not an unrelated current driver.

Offline processing preserves recent preceding live identity observations (up to one second beyond a recorded observation); frames lacking reliable historical context are explicitly unknown. Dual recordings compress consecutive identity observations into frame intervals. Supabase object upload and Firebase's other fields/transports remain intact. Temporal fatigue events now capture event-time identity, video offsets and fresh GPS through a local aggregate event store; see [fatigue monitoring](fatigue_monitoring.md).

MediaPipe runs on the selected driver's crop in VIDEO mode with increasing timestamps. Its stable measurement result contains `face_visible`, `eyes.left_opening_ratio`, `eyes.right_opening_ratio`, `mouth.opening_ratio`, `head_pose.pitch/yaw/roll`, `quality` and sample time. Ratios use pixel-space distances to account for aspect ratio. Pose is approximate, in degrees, derived from the canonical-to-observed face matrix; camera-relative neutral pose is not calibrated. UI therefore does not claim that the head is forward. The temporal fatigue service now consumes these measurements for calibrated heuristic warnings. No heavier fatigue classifier is used; see [fatigue monitoring](fatigue_monitoring.md) for thresholds and validation limits.

## Validation and resources

Run:

```sh
python -m compileall -q core services models tests scripts streamlit_app.py api_app.py main.py
python -m unittest discover -s tests -v
git diff --check
python scripts/benchmark_driver_monitoring.py
python scripts/benchmark_driver_monitoring.py --image /path/to/consented-cabin-frame.jpg
```

The benchmark prints aggregate timings/RSS only; it does not open a camera, query the enrolled database or save face data. AppTest exercises public/admin navigation and reruns with cloud/camera work mocked. Unit tests cover missing/one/multiple faces, corrupted vectors, unknown/disabled/deleted drivers, temporal confirmation, mismatch/loss/expiry, unavailable models/database, enrollment quality, metadata and worker freshness. Actual camera and cloud acceptance checks remain manual.

Measured locally on Python 3.14.3, 16 logical CPUs, without face weights or a running recorder:

| Probe | Result |
|---|---|
| Bare Python RSS | 23.38 MiB |
| Roadwatch import RSS | 402.98 MiB |
| Roadwatch import time | 16.744 s, includes existing dependencies |
| Idle CPU, 0.5-second observation | 0% of one logical core; short sample only |
| Disabled monitoring RSS | 403.86 MiB |
| Disabled API call mean, 1,000 calls | 0.0165 ms |
| Face model initialization while disabled | none |
| Loaded-model RAM / native detection / recognition / landmark CPU and latency | unmeasured: weights and optional runtime unavailable |

These are an import baseline, **not** full idle Streamlit, active YOLO, encoding or camera numbers. No measured native inference results are claimed. As a planning allowance, reserve roughly 150–300 MiB of extra RAM for native runtime, weights and activations until measured; that is an unvalidated budget, not a benchmark. Model assets add tens of MiB, with SFace the largest. The Windows MediaPipe wheel alone is about 20 MB compressed; Linux x86-64 about 38 MB, before dependencies and extraction, according to the inspected PyPI metadata.

Estimate active CPU duty from measured latencies using `5 * YuNet_ms + 10 * landmark_ms + 0.5 * SFace_ms` milliseconds of work per second while unverified; use 1 for candidate recognition and 1/7 after verification. Idle and no-face modes do less work. Native parallelism, resizing, USB capture, Firestore and rendering add overhead, so this is only a first-order budget. OpenCV's global thread count is deliberately unchanged because YOLO/recorder paths share the process. Models remain cached through recording stops and normal reruns. Final service shutdown closes the landmark runtime and ends its workers; restart the process to reinitialize after shutdown. High read latency can delay landmarks in the single monitoring worker, but cannot block recording. Monitor capture FPS, queue drops and encoding while enabling each stage before adding heavier AI.

## Manual acceptance checklist

- [ ] Install optional runtime and all three binary weights; verify native API imports on Windows and deployment Linux/Python.
- [ ] Deploy and emulator-test restrictive Firestore rules; confirm ordinary clients cannot read/write either driver collection, including through wildcard rules.
- [ ] Confirm a driver-facing camera works; verify front/rear index collision is rejected and road-camera frames never enter recognition.
- [ ] Calibrate threshold, seat region and quality gates using consented validation data.
- [ ] Add and enroll Driver A from five clear, varied samples; ensure no raw face images are created.
- [ ] Driver A becomes verified only after three spaced checks; another person remains Unknown Driver.
- [ ] Driver A leaves briefly: cached identity survives; after five seconds absent the session becomes lost.
- [ ] Driver A returns: three fresh checks restore recognition with a new session ID.
- [ ] Start recording after verification and inspect `driver_id`/status/session in video start metadata.
- [ ] Change drivers mid-recording; verify event/GPS/incident context changes while start metadata stays fixed.
- [ ] Upload processed video; verify optional identity fields in Firebase and unchanged Supabase playback.
- [ ] Admin can list, enroll, re-enroll, disable, enable and delete only biometric enrollment. Viewer/operator cannot manage drivers.
- [ ] Disable/delete during enrollment and after verification; verify pending enrollment cannot restore the deleted/disabled identity.
- [ ] Test glasses, low light, head turning, passengers and two similarly sized faces.
- [ ] Test automatic startup, Streamlit reruns, navigation, repeated recording cycles, camera unplug/reconnect, and final service shutdown. Confirm no manual recognition controls appear.
- [ ] Remove each model and disconnect the private database; recording/GPS/incidents continue.
- [ ] Repeat with YOLO/OCR/tracking and compression active; measure CPU, RAM, inference latency, camera FPS and dropped samples over a representative trip.

## Background stability validation

Run `python scripts/stability_driver_monitoring.py --seconds 180 --output docs/driver_monitoring_stability.json` to exercise real background threads, CameraChannel lifecycle, a bounded frame queue and the cache using synthetic camera/model/database adapters. The adapters deliberately run slower than the producer, which forces old-frame dropping. This creates no biometric images, camera access or network traffic. It does not establish native inference performance or real-device USB behavior.

The aggregate results are in `driver_monitoring_stability.json`. The first three-minute run completed with capacity/peak queue depth 1, 283 old frames dropped, one synthetic camera open/release, one initialization of each model adapter, one simulated database read and one verification write across 351 rerun simulations. Warm RSS stayed between 347.39 and 347.45 MiB, ending 0.01 MiB above the warm starting sample. Mean CPU was 3.63% of one logical core; this measures orchestration plus synthetic work, not native models. Peak process threads were baseline +3 (capture, producer, worker); all monitoring threads stopped. Other preexisting background threads exited during the run, so total process threads decreased from 22 at baseline to 16 after shutdown. A final repeat records the latest measurements in the JSON artifact.

Additional tests cover existing-camera adoption, writer attachment without new capture, automatic startup calls, absence of normal-user controls, queue replacement, cache TTL/revision/backoff, adaptive idle scheduling, cached identity grace, consistent mismatches and controlled worker/camera recovery.

## Next phase

Deployment preflight/calibration tools, temporal fatigue detection and linked local warnings/events are implemented. Native model setup and real-camera calibration remain incomplete. See [the three-phase report](fatigue_monitoring.md) and `driver_deployment_validation.json` for current readiness.

## Change inventory and completion status

Created:

- `services/driver_monitoring/__init__.py`, `config.py`, `face_detection_service.py`, `face_recognition_service.py`, `face_enrollment_service.py`, `face_landmark_service.py`, `driver_monitoring_service.py`, `driver_session_service.py`, `biometric_store.py`, `runtime.py`, `metadata.py`, `ui.py`, `embedding_index.py`, `inference_scheduler.py`.
- `models/driver_identity.py`.
- Nine test modules: `test_face_detection_service.py`, `test_face_recognition_service.py`, `test_face_enrollment_service.py`, `test_face_landmark_service.py`, `test_driver_session_service.py`, `test_driver_monitoring_service.py`, `test_driver_monitoring_integration.py`, `test_driver_monitoring_ui.py`, `test_driver_background.py`.
- `requirements-driver-monitoring.txt`, `scripts/benchmark_driver_monitoring.py`, `scripts/stability_driver_monitoring.py`, the aggregate stability JSON report, this guide and `docs/driver_monitoring_firestore.rules`.

Modified for narrow integration: `core/opencv_recorder.py`, `core/dual_camera.py`, `services/video_service.py`, `services/firebase_service.py`, `services/incident_service.py`, `services/gps_service.py`, `services/dual_session_service.py`, `services/video_processing_service.py`, `services/dual_video_processing.py`, `streamlit_app.py`, `.env.example`, `.gitignore`, `README.md`.

Validation: 83 tests passed (16 existing plus 67 subsystem tests); Python compilation passed. Application-scope `git diff --check` passed. The full repository check remains nonzero because three pre-existing lines (410, 411, 421) in `.codex-sheet-work/node_modules/playwright-core/types/protocol.d.ts` contain trailing whitespace; this unrelated dependency file was not edited. No model binaries, credentials, raw enrollment images or biometric records were added. No local machine paths were introduced in application code.

Implementation is ready for native setup and acceptance testing, not a claim of production biometric accuracy. Still required: finish the optional package install, obtain model weights, configure/calibrate the cabin camera and threshold, deploy restrictive Firestore rules, enroll consented drivers, test real cloud round trips and benchmark native inference alongside YOLO. Temporal fatigue scoring and a neutral-pitch calibration helper are implemented; real-device calibration remains pending.
