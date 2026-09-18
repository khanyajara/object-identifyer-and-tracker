# Camera repair report — 18 September 2026

Repository implementation and synthetic verification are complete. Production
deployment and physical-camera acceptance remain unverified. The deployed site
redirected to Streamlit authentication, which this environment could not load
(`ERR_SOCKET_NOT_CONNECTED`). Therefore this report distinguishes confirmed code
defects from an unconfirmed production ICE/permission/deployment problem.

## Confirmed root causes and audit

The repository ALREADY contained browser WebRTC. On Linux without video devices,
`core/capture_mode.py::browser_capture_enabled` already selected it. It would be
incorrect to claim that the default Cloud path necessarily called VideoCapture(0).
An explicit legacy local override could still select server-side capture.

Confirmed defects:

1. `CameraChannel.ensure_capture` released its existing handle before reopening.
   For a stalled browser channel that handle is the current BrowserFrameSource.
   Closing it makes subsequent offers ineffective and reopening fail forever.
   BrowserCameraChannel now preserves the active peer source during recovery.
2. `BrowserCameraChannel.new_connection` replaced the source while the old reader
   could remain alive. The new implementation stops/joins that reader before
   replacement and retains per-peer source ownership against late callbacks.
3. `streamlit_app.py::dash_cam_page` called cached_model/cached_ocr before rendering
   the browser component, and DualCameraUIManager initialized VisionPipeline
   synchronously. Slow/failed model initialization could prevent camera startup.
   Browser inference is now initialized in its own session worker with backoff.
4. Browser configuration enabled front and rear channels while
   `render_browser_cameras` rendered only rear. The UI offered the nonexistent
   front feed, attempted to record it, and treated a missing composite as failure.
   Browser mode now enables/displays one actual feed and accepts single-camera
   processing success.
5. `DualCameraManager.start_recording` and dual post-processing still requested
   WebM/VP8/VP9 and omitted the existing compression path. They now use MP4 and
   propagate validated compressed output into existing upload/playback metadata.
6. Browser driver monitoring was explicitly skipped to avoid the process-global
   runtime leaking identity across sessions. It now has a session-owned runtime
   consuming the same source, with idle shutdown and isolated identity/context.
7. Browser status had no first-frame timeout or visible received/dropped counts.
   Roadwatch now reports a 20-second startup timeout and stale/recovery states.
8. Browser lifecycle controls joined AI without a timeout. Capture controls now
   proceed independently and discard results from an earlier lifecycle.

Other findings: UI delegates capture to services; AI consumes snapshots/queues;
the existing buffer and road-AI queue were already bounded. There was no need to
add st.camera_input, camera-index scanning or a second WebRTC implementation.
Recorder writes are independent of AI/cloud calls. Native browser errors remain
visible in the existing component. Requirements match the installed Streamlit
and WebRTC versions. Linux library/config files were reviewed and retained.
No production logs were accessible to confirm a particular TURN/permission error.

The audit searched all tracked project sources plus application directories.
Windows denied access to several unrelated temporary/cache directories; these
were not modified. No AGENTS.md was found in the project.

## Old and new architecture

Old local: UI could choose an independent legacy CameraManager or dual channels;
driver monitoring could provision a dedicated extra camera. Old Cloud: WebRTC
rear source -> dual channel -> preview/recording/road AI, with synchronous model
startup, a phantom enabled front source and no browser driver consumer.

New Cloud: browser video-only capture -> PyAV BGR normalization -> size-one
BrowserFrameSource -> CameraChannel -> latest preview, MP4 writer, sampled road
AI and independently scheduled driver AI. All mutable state belongs to the
browser session. No browser frame is sent to a global camera/driver singleton.

New local UI: configured OpenCV handles -> LocalOpenCVCameraSource -> the same
CameraChannel pipeline. One owner per configured device, even if both configured
indices match. Driver monitoring reuses a rig channel; the old dedicated setting
no longer opens an additional webcam. Standalone calibration utilities remain
explicit local-camera tools. The legacy recorder class remains for compatibility
but is no longer selected by the UI and rejects browser mode.

## Files modified

- `.env.example`: backend/ICE examples and shared driver source configuration.
- `core/browser_camera.py`: normalization, metrics, health, peer recovery.
- `core/capture_mode.py`: ROADWATCH_CAMERA_BACKEND with legacy compatibility.
- `core/dual_camera.py`: common source adapter, one browser feed, MP4, session
  driver runtime, local duplicate-index guard, nonblocking browser lifecycle.
- `core/opencv_recorder.py`: prevent legacy recorder use in browser mode.
- `services/browser_camera_ui.py`: bounded receiver, conversion errors, health UI.
- `services/driver_monitoring/runtime.py`: shared local source and browser idle exit.
- `services/dual_video_processing.py`: MP4 output and existing compression service.
- `services/dual_session_service.py`: accurate format and compressed upload fields.
- `streamlit_app.py`: shared UI route, lazy browser AI, correct single-feed display
  and processing success.
- `scripts/verify_browser_transport.py`: real MP4/compression transport smoke test.
- `docs/browser_camera.md`: deployment, permission recovery and acceptance steps.
- `docs/driver_monitoring.md`: shared-source configuration clarification.

## Files created

- `core/frame_source.py`: structural CameraSource interface and local adapter.
- `core/lazy_vision_pipeline.py`: isolated, serialized browser inference initialization.
- `tests/test_camera_repair.py`: camera-free source, recovery, component, AI,
  driver lifecycle, duplicate-owner and metadata regression tests.
- `docs/camera_repair_report.md`: this report.
- `tmp/camera-transport-report.json`: generated synthetic verification results.

The pre-existing user change to data/gps/gps_history.json was left untouched.
No packages were added/removed. requirements.txt retains Streamlit 1.58.0 and
streamlit-webrtc 0.77.0. No PyTorch/TensorFlow installation was performed.

## Every remaining VideoCapture call

All 13 calls remain for local hardware, compatibility or saved-video reading.
None is reached for browser webcam capture.

| File:line | Purpose |
| --- | --- |
| core/dual_camera.py:477 | The configured local device owner; platform backend fallback for that same index. |
| core/opencv_recorder.py:156 | Legacy local recorder, no longer selected by Streamlit UI. |
| core/opencv_recorder.py:159 | Legacy local backend fallback; browser guard applies. |
| core/video_io.py:312 | Validate/read a saved video file. |
| scripts/collect_driver_calibration.py:53 | Explicit standalone local calibration utility. |
| scripts/collect_recognition_samples.py:38 | Explicit standalone local sample-collection utility. |
| scripts/verify_browser_transport.py:66 | Decode the synthetic saved MP4. |
| services/compression_service.py:23 | Read saved video to create a thumbnail. |
| services/dual_video_processing.py:54 | Validate saved source video. |
| services/dual_video_processing.py:90 | Read saved video for annotation. |
| services/dual_video_processing.py:262 | Read saved front video for composite. |
| services/dual_video_processing.py:263 | Read saved rear video for composite. |
| services/video_processing_service.py:107 | Legacy saved-video processing path. |

## Tests and performance

Final suite: 181 tests run, 180 passed and one skipped (31.457 seconds).
Python compilation and git diff --check passed. The final tests include the
shared local driver source and duplicate-index protection. Windows sandbox
temporary-directory permissions required running tests outside the sandbox;
the resulting full run completed successfully.

The final synthetic transport run received 164 WebRTC frames, recorded 62,
dropped one pending frame, decoded the recording, compressed successfully with
libx264, and stopped the capture worker. Measured recording throughput was
30.85 FPS for the synthetic 30-FPS source. Arrival count includes frames outside
the recording interval; unrecorded frames are not all buffer drops.

Production browser constraints are 640×480 at a maximum 15 FPS; road inference
samples at up to 2 FPS. Driver monitoring retains configured independent
YuNet/SFace/MediaPipe schedules. These are targets, not measured deployment FPS.
The 1,000-frame overwrite test retains one pending frame and at most 60 timing
samples (999 older frames discarded). Repeated ensure_capture calls reuse the
same live worker. Driver source expiry stops both driver threads.

CPU load, long-duration process RSS and physical-camera FPS were not profiled.
The bounded-buffer tests establish queue bounds, not a whole-process memory
leak guarantee. Models consume per-session memory; Cloud capacity still matters.

## Manual actions and limitations

Follow docs/browser_camera.md for exact Windows and deployed acceptance steps:
deploy changed/new files to the configured Cloud branch, reboot, allow video
permission, verify preview/reruns/recording/AI, process/compress, upload through
the existing action, verify Firebase and admin cloud playback, then test a second
session and disconnect/recovery. Optionally set ROADWATCH_CAMERA_BACKEND=browser.
Configure provider TURN credentials if the deployed network cannot connect with
STUN alone; no working production relay was available for verification here.

Permission-denied/missing/busy details are rendered by the existing component;
its Python API does not expose those errors as distinct telemetry states.
Transport renegotiation uses the visible Retry action; fresh-frame recovery is
automatic without a rapid reconnect loop. Driver recognition still needs its
existing assets, enrollment, calibration and storage configuration, and requires
a cabin-facing source. Native inference already running cannot be forcibly killed.

No physical webcam, production Supabase upload, Firebase write, live deployment
or remote admin playback was verified. Their existing mocked regressions passed;
that does not substitute for the listed deployment acceptance test.
