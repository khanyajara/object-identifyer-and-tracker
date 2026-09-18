# Browser camera repair and deployment

Roadwatch keeps streamlit-webrtc 0.77.0 and Streamlit 1.58.0. No new dependency
is required. Browser sessions request video only at 640×480, up to 15 FPS.

Each session owns its WebRTC stream, latest-frame buffer, recording and AI.
PyAV converts frames once to uint8 BGR H×W×3. CameraChannel reads the size-one
buffer, writes recording frames, publishes the latest preview, and samples a
size-one road-AI queue. Driver monitoring samples the same channel snapshot.
The historical rear role identifies this single browser feed in metadata.
Local driver AI also shares the configured rear/front capture channel; the
legacy dedicated setting no longer opens a third camera. Driver analysis needs
a cabin-facing view, regardless of backend.

## Configuration

ROADWATCH_CAMERA_BACKEND defaults to auto: headless Linux (including Community
Cloud without /dev/video devices) uses browser capture; Windows uses OpenCV.
Explicit values are browser and opencv. Legacy CAMERA_INPUT_MODE settings still
work when the new setting is absent.

For an explicit Cloud setting, add this top-level Streamlit secret:

```toml
ROADWATCH_CAMERA_BACKEND = "browser"
```

Keep existing Firebase/Supabase secrets, pinned requirements and Python 3.12.
No Linux webcam driver can expose a visitor's camera. Existing Linux libraries,
CORS/XSRF protection and HTTPS remain in place.

Default ICE uses Google's public STUN server. Some hosting/client networks
require TURN even when permission succeeds. Add your provider's configuration
as a top-level secret (replace these placeholders):

```toml
ROADWATCH_WEBRTC_ICE_SERVERS = '[{"urls":["stun:stun.l.google.com:19302"]},{"urls":["turns:YOUR_TURN_HOST:443?transport=tcp"],"username":"YOUR_USERNAME","credential":"YOUR_PASSWORD"}]'
```

TURN credentials necessarily reach the browser as ICE configuration. Prefer
short-lived provider credentials; never commit them.
See [upstream deployment documentation](https://github.com/whitphx/streamlit-webrtc#serving-from-remote-host).

## Health and permissions

The existing component displays native browser permission/device errors.
Its Python API exposes playing/signalling, not the browser DOMException.
Roadwatch does not mislabel an ICE/network failure as permission denial.

| State | Display/recovery |
| --- | --- |
| camera_permission_required | Allow this site's camera permission. |
| camera_permission_denied | Native component error; allow camera access in browser site settings and reload. |
| camera_not_found | Native device error; connect/select a camera. |
| camera_busy | Native device error; close competing camera apps. |
| camera_starting | Signalling/playing, waiting for the first Python frame. |
| camera_active | Fresh frames; dimensions and received/dropped counts displayed. |
| camera_stale | No frame for two seconds; suppress old preview. |
| camera_reconnecting | No frame for five seconds or source ended; fresh frames restart one worker. |
| camera_error | First-frame timeout after 20 seconds, invalid ICE or conversion failure. |

Denied/not-found/busy remain frontend states, not separate Python telemetry.
Open / Retry Preview starts a fresh peer after failed negotiation. Stop any
pending recording first. There is no automatic permission-request or ICE restart
loop. Change camera through the component selector/browser device settings.

Same-peer recovery no longer releases the current source. Peer replacement stops
the old worker; late old callbacks cannot close the new source. Driver workers
exit after 15 seconds without fresh source frames. Browser recording controls
do not wait for YOLO initialization; old inference results are discarded after
lifecycle changes. Already-running native inference cannot be forcibly cancelled.

## Recording and AI

New originals and annotated output use MP4 intermediates. CompressionService
produces and validates playback exports, preferring H.264/faststart. Metadata
preserves compressed_processed_path and upload_video_path for the existing
Supabase upload, Firebase metadata and admin playback path. Failed compression
retains the original and reports fallback. Existing upload controls remain
responsible for publishing recordings.

YOLO initializes on its worker, with a 30-second retry delay on initialization
failure. Mutable model/tracker state belongs to each browser session. Road AI
samples at up to 2 FPS; driver AI retains its configured independent scheduler.
Driver model assets, optional MediaPipe dependencies, recognition calibration,
enrollment and private biometric storage still need their existing setup.
Unavailable models do not prevent capture or recording.

## Automated verification

```powershell
venv/Scripts/python.exe -m unittest discover -s tests
venv/Scripts/python.exe scripts/verify_browser_transport.py --output tmp/camera-transport-report.json
venv/Scripts/python.exe -m compileall -q core services tests scripts streamlit_app.py
git diff --check
```

The transport test uses two local aiortc peers and synthetic frames, records and
decodes MP4, runs playback compression and verifies worker shutdown. It does not
access a webcam, exercise browser permissions/TURN or upload media. Synthetic
delivery can burst after scheduling delays; its FPS is not a webcam benchmark.

## Deployment and physical-camera acceptance

1. Deploy all changed/new files to the branch configured in Community Cloud,
   including core/frame_source.py and core/lazy_vision_pipeline.py. Reboot the
   app and inspect startup logs. Keep the pinned requirements.
2. Open https://dashcamz.streamlit.app/, log in and open Live Dash Cam. Allow
   camera permission; microphone permission must not be requested.
3. Verify continuous preview and rising received count within 20 seconds.
   Confirm a first-browser-frame log and no local camera-index attempts.
4. Record for 15 seconds. Trigger a dashboard rerun; capture and recording
   must continue without duplicate camera or driver workers.
5. Stop. Verify original, annotated and compressed MP4 files. A single feed
   must succeed without requiring a dual-camera composite.
6. Use the existing upload action. Verify Supabase upload, Firebase video
   metadata and playback from the admin cloud view.
7. In a second session, deny permission then recover through browser settings.
   Confirm frames, recording and driver identity do not cross sessions. Test
   missing-camera and busy-camera errors where practical.
8. Interrupt networking, check stale preview suppression and preserved recording.
   Stop Recording and Retry Preview. If permission succeeds but no frames arrive,
   configure TURN and repeat on the affected network.
9. On Windows use auto/opencv and run
   venv/Scripts/python.exe -m streamlit run streamlit_app.py.
   Verify preview, recording, AI, stop, processing, compression and upload.

Live acceptance remains required. This environment could not load Streamlit's
hosted authentication redirect (ERR_SOCKET_NOT_CONNECTED). No production deploy
or production upload was performed.
