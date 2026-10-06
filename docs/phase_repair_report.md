# Phase repair status — 6 October 2026

The existing implementation was repaired in place. No existing features, recordings, accounts, calibration values or phase implementations were removed. Shared summary logic now serves single and dual recordings, rather than maintaining two counting implementations. Real-sample calibration remains pending at the user's request.

## Phase 1: native setup and calibration

- YuNet loads and runs inference; SFace and MediaPipe landmarker load successfully.
- Native preflight reports `software_ready=true`, names each pending calibration setting, and provides next steps. Camera-source readiness now respects the application's local settings as well as environment configuration.
- Recognition, eye-closure, mouth-opening and neutral-pitch thresholds remain unset. No values were guessed and no alerts were enabled by changing thresholds.
- Added the missing test dependency manifest and installed pytest in the project virtual environment. Explicitly declared python-dotenv, already installed and used by the setup/calibration scripts.
- Corrected malformed trailing characters in the existing local Firebase client-email setting and the Supabase base URL (API paths are appended by the service). Authenticated cloud access remains unverified.

## Repairs across existing components

- Restored completed-video validation and the upload result contract; missing/empty files fail clearly. The finalizer returns the actual completed path, fixing the original offline check.
- Retained the existing record-based upload API and added the exact-source API expected by the existing transaction. Exact-source uploads verify remote object size before metadata acknowledgement. The dashboard uses this verified path, preserves retry sources, and reports incomplete metadata sync instead of false success.
- Single/dual recordings preserve the owner's UID. New account recordings use account directories, filenames include unique suffixes, and dual video IDs are accepted without loosening traversal checks. Existing ownerless evidence remains available to administrators and is not assigned to the current user.
- Local/cloud video catalogues and the API filter by account ownership; local playback checks ownership before requesting a cloud URL. Cloud inventory/reconciliation preserves owner metadata. Signing out or changing account finalizes current evidence and clears the previous account's capture session.
- Movement uses independent camera baselines and resets on resolution changes. OCR uses independent camera clocks, runs on the first eligible frame, and resumes when a media timeline resets. Lazy AI loads the existing OCR reader and can fork fresh recording state.
- Shared YOLO inference is serialized, invalid frames fail clearly, and missing local weights do not initiate an accidental download. Internal inference TypeErrors are reported without a second inference attempt.
- Smooth preview uses current frames with fresh, dimension-matched overlays. AI errors remain visible in combined previews.
- The dashboard now has a Live AI switch. Pausing disables live object/OCR/tracking and driver inference without closing capture or stopping recording, clears stale overlays/warnings, and discards inference results from before the switch. Saved-video processing remains independent. Per-camera measured FPS, target FPS, resolution and status support an on/off comparison; the dashboard preview remains capped at 15 FPS. Local capture settings now request 1280×720 at 30 FPS. Reopen capture to apply resolution changes; actual device FPS requires a physical-camera check.
- Processing rejects AI error events, releases capture when writer creation fails, clears stale upload paths, and blocks uploads after processing/compression failure. Originals and completed processed evidence remain on disk.
- Single and dual recordings use one summary algorithm for unique tracks, peak counts, plates and movement episodes.

## Verification

Automatic upload follow-up: single and dual Stop Recording now queue completed
compressed camera videos through the existing upload worker. Active jobs are
deduplicated, verified uploads save playback URLs before cloud metadata sync,
and signed URL expiry is retained and refreshed. Compression saves input/output
sizes without deleting evidence. Also repaired nullable incident timestamps
exposed by the storage cleanup regression. After these fixes, 51 relevant tests
and 2 subtests passed, followed by 6 automatic-upload checks (including the dual
stop hook). An external upload/playback test was blocked by automatic approval
review because it would export an existing private recording; it remains pending
explicit approval for that specific video. No remote upload was performed.

### Follow-up module audit

The follow-up audit connected media, JSON records, dual sessions, post-processing and the sync worker to the existing shared runtime storage directory. Previously, cloud staging and these consumers could disagree about where files were stored. Explicitly supplied camera directories and existing local storage remain supported.

The sync API now persists validated metadata with stable recording IDs, retains owners, ignores incoming filesystem paths, and rejects malformed data before writing. Concurrent metadata status updates share the existing per-file locks and preserve both the central log and sidecar. Dual session/video path helpers reject traversal. Supabase services normalize REST endpoint URLs to the project origin. Dual records expose library timestamps, legacy null report fields are handled, and single-camera processing releases failed decoders and respects the tracking setting.

The module import audit passed for all 62 core/service modules. The offline check now also runs the real dual-camera processing and compression path on local sample video, producing six annotated frames and playable output. No physical camera or remote service was used.

- Latest full regression suite: **241 passed**, zero failures or collection errors, plus 9 passing subtests. Five targeted Live AI checks also cover capture preservation, paused inference, stale-result rejection, driver warning suppression and per-camera dashboard FPS. The earlier repair checkpoints remain covered.
- Original `scripts/offline_vision_check.py`: passed without the earlier diagnostic workarounds. Local YOLO detected the bundled bus/person samples, tracking IDs remained stable on repeated frames, EasyOCR loaded, and MoviePy produced a playable MP4.
- Native driver preflight: software checks passed; camera acceptance remains false because calibration is pending.
- Syntax checks passed for 108 Python files; `git diff --check` passed.
- The two older test fixtures were corrected to supply a valid BGR image and explicitly mock successful compression; their original assertions remain. Existing detection-audit, upload and ownership tests were retained.

The FastAPI test-client dependency emits a deprecation warning. `pip check` still flags MediaPipe's declared `opencv-contrib-python` dependency: the optional setup intentionally keeps the pinned `opencv-python` distribution to avoid two packages replacing the same cv2 module. Native inference checks pass; no conflicting wheel was installed.

## Remaining acceptance work

Calibration is intentionally pending. Physical cabin/main camera operation, representative recognition/fatigue accuracy, long-duration native performance, authenticated cloud uploads/access rules, and external notifications require real-device or service acceptance. Automated/mock tests and bundled sample images do not establish those results.

Run checks in the project virtual environment:

```powershell
.\venv\Scripts\python.exe -m pytest tests -q
.\venv\Scripts\python.exe scripts/offline_vision_check.py
.\venv\Scripts\python.exe scripts/validate_driver_deployment.py --output docs/driver_deployment_validation.json
```

The final command intentionally returns a nonzero readiness status while calibration is pending. Historical failure details remain in `docs/debug_analysis.md`; latest full test output is in `complete-repair-tests.log`, and earlier results remain in `phase-repair-tests.log`.
