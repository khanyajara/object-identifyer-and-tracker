# Recording pipeline repair

## Evidence and scope

- `data/videos/recording_2026_09_10_100822.json:32-33` records a failed upload for `vid_20260910_100822_fd9142`; lines 4272-4276 report successful compression and 246 processed frames. These are historical metadata claims, not proof that the file survives.
- The original, processed, compressed-original and compressed-processed files referenced by this record are absent. The repository data tree contains its JSON/JSONL, snapshots and thumbnail only. Those artifacts cannot recover the original video.
- Before this repair, `StorageService.cache_cleanup_preview` treated compressed processed files as disposable without requiring upload/metadata success. This is a confirmed deletion mechanism, but there is no deletion audit proving it caused September 10's loss.
- Before this repair, `retry_processed_video_upload` queued a retry without source validation and `SupabaseService.upload_processed_video` selected a metadata path directly. A stale Windows path therefore failed repeatedly. No evidence shows that a guessed compression filename caused this incident: the previous compressor already returned a path.
- Compression failures could fall back to an uncompressed MP4 and still upload. Upload success was inferred from POST success, without checking the stored object. Firestore unavailability could return local mode while the UI still said Synced. These are confirmed code defects, not proven causes of the missing September file.
- Existing worktree edits were preserved. No detector/tracker algorithm, WebRTC setup, credentials, or UI layout was changed. No browser was opened. External services were faked for tests.

## Runtime behavior

Local OpenCV capture and the browser-fed `BrowserCameraChannel` keep their capture implementations. Both release writers before processing. The single-camera processor returns the exact validated compression output as a Path; dual-camera results carry the actual compression output. Queue workers receive that explicit path. Retried uploads load only the local record and require an existing, nonempty, successfully compressed source; Firestore filesystem paths are never used as upload inputs.

Shared upload order: validate file -> Storage POST -> verify exact object name and byte size with Storage list -> create playback URL -> Firestore acknowledgement -> mark the workflow uploaded -> remove disposable cloud staging. Storage success with a Firestore failure is recorded as `supabase_upload_status=uploaded`, `upload_status=metadata_pending`, `cloud_ready=false`; sources remain. Processing, compression, upload or verification failure cannot complete the workflow. Local files remain, and cache cleanup protects files until Storage and Firestore have succeeded.

Firestore receives bucket/object key, remote playback URL, completed processing status and byte size. URL refresh uses the stable object key. Private signed URLs expire; callers must continue using the existing CloudPlaybackService refresh route. Local JSON may contain local working paths; those are not durable cloud locations.

`core/storage_paths.py` keeps project-local `data/` on local machines. Auto mode uses OS temporary storage on headless Linux (Linux without `/dev/video*`), matching the existing capture auto-detection assumption. Set `ROADWATCH_STORAGE_MODE=temporary` explicitly on Streamlit Community Cloud; use `local` to override for a headless local Linux machine. No durable staging was implemented: cloud temporary files and their retry records can disappear on restart. A failed upload can be retried only while its source exists; otherwise restore a backup and reprocess.

The repository uses requests and Storage REST endpoints, with no Supabase Python SDK declared in requirements. Verification therefore uses the already-implemented `list_objects` API and checks exact filename and size. It is not a checksum comparison and cannot prove codec compatibility or recover from a network timeout that occurred after the server committed an object. Such uncertain outcomes retain the source and remain retryable through the idempotent object key/upsert.

## Remaining configuration and WebRTC findings (unchanged)

- Supabase URL, server-side key, bucket, upload/upsert permission, list/read permission and signed-URL permission must be configured. Verification requires listing even if uploading is allowed. Keep keys in deployment secrets; never ship privileged keys to the browser. Firestore credentials and write permissions for the configured video collection are required before staging cleanup.
- `requirements.txt:2` pins streamlit-webrtc 0.77.0. `services/browser_camera_ui.py:26` imports its frontend component, and line 58 creates the SENDONLY component. Component-loading failures precede camera/media transport; inspect deployment package installation, component asset responses, browser console and Streamlit logs separately. No supplied log establishes an asset-loading cause.
- `services/browser_camera_ui.py:8` defaults to a STUN server and supports `ROADWATCH_WEBRTC_ICE_SERVERS`; no TURN relay is configured by default in code. Media connection failures can involve browser camera permission/secure context, device availability or ICE/firewall reachability. These are diagnostic possibilities, not confirmed causes. Collect ICE state and server/browser logs and configure an appropriate relay if needed. No WebRTC configuration was changed or live connectivity tested.

## Safe September 10 recovery/update procedure (not executed)

1. Use authorized access to read and export the document `videos/vid_20260910_100822_fd9142` (substitute the configured FIREBASE_VIDEO_COLLECTION). Preserve its current update time and fields. Do not treat a permission/network error as absence.
2. Check the recorded Supabase bucket/key first, if present. Then paginate Storage listings under `processed/vid_20260910_100822_fd9142` in the actual configured bucket, and any documented legacy bucket/prefix. Do not infer remote absence from this offline JSON's null fields. Download and decode any candidate before declaring it usable.
3. Check the source machine, its backups and OneDrive recovery history for `recording_2026_09_10_100822.mp4`, `recording_vid_20260910_100822_fd9142_processed.mp4`, and their compressed versions. A thumbnail is not a video backup. If an original survives, reprocess it; if a verified cloud object survives, repair metadata with its real bucket/key and a newly generated playback URL.
4. Only after successful authorized checks establish that neither usable local/backup video nor a remote object exists, transactionally re-read the Firestore document. Abort if its update time, bucket/key or upload state changed since inspection. Merge the following fields, preserving detection/evidence metadata:

```python
{
    "status": "unavailable",
    "upload_status": "unavailable",
    "supabase_upload_status": "unavailable",
    "cloud_ready": False,
    "unavailable_reason": "No usable source or Supabase object after verified recovery checks",
    "updated_at": firestore.SERVER_TIMESTAMP,
    "playback_source": None,
    "supabase_url": None,
    "supabase_processed_url": None,
    "supabase_mp4_url": None,
    "supabase_webm_url": None,
    "cached_playback_url": None,
    "playback_url_expires_at": None,
}
```

5. Retire any pending local queue entry for that ID as failed/unavailable and clear only its stale local upload fields; retain diagnostic metadata. The repaired retry entry point already refuses a missing source before creating a task. Do not manufacture a new local filename or repeatedly retry the old Windows path.

No live Firestore or Supabase access was attempted, and no live record was updated. Recoverability is unresolved remotely and unavailable from the present local video files.

## Files changed for this repair

- `core/storage_paths.py`, `core/video_io.py`, `core/dual_camera.py`: runtime staging, output validation/finalization, shared capture directory.
- `services/video_service.py`, `services/local_json_service.py`, `services/dual_session_service.py`: consistent runtime storage roots for media and local metadata.
- `services/video_processing_service.py`, `services/dual_video_processing.py`: actual output handoff, explicit processing/compression failures, source retention.
- `services/supabase_service.py`, `services/recording_upload_service.py`: typed path-based upload, object verification, metadata commit and cleanup ordering.
- `services/firebase_service.py`, `services/storage_service.py`, `streamlit_app.py`: remote playback metadata, protected retry sources and worker/queue integration.
- `tests/test_recording_upload_pipeline.py`, `tests/test_dual_video_processing.py`, `tests/test_auth_and_api.py`, `tests/test_supabase_database_sync.py`: focused fake-service coverage and updated upload contract.
- This report. Other files already modified when work began are outside this repair.

## Validation

`python -m pytest tests/ -k "path or upload or compression or status"`: **28 passed, 178 deselected**, one existing Starlette/httpx deprecation warning. Executed with installed project site-packages on PYTHONPATH and system pytest; the venv has app dependencies but no pytest. The initial system-only attempt failed collection on missing bcrypt/Streamlit, and sandbox temporary-directory permissions required running the offline tests outside that sandbox.

Additional affected tests (`tests/test_dual_video_processing.py` and `tests/test_recording_upload_pipeline.py`): **23 passed, 2 subtests passed**. Syntax parsing and `git diff --check` passed. Tests exercise missing/empty sources, native Windows paths and simulated Linux rejection of Windows paths, explicit output paths, writer release, processing/compression errors, upload/verification errors, metadata status transitions, temporary cleanup order and remote-only Firestore playback fields. Linux was simulated, not run on a Linux host. Real encoding, hardware capture, WebRTC, live Storage/Firestore and Streamlit Cloud deployment remain untested.

## Account ownership follow-up

New recordings freeze the authenticated account UID at capture start and persist `uid == user_id` through single/dual camera metadata, processing, upload and Firestore. Driver recognition IDs are separate. The existing username-based authentication now exposes a stable Roadwatch UID (UUID5 derived from the canonical username for legacy accounts); explicitly configured account UIDs are preserved. This is an application UID, not a claim that Firebase Auth or Supabase Auth is installed. Usernames currently serve as immutable account keys; username renames/account recreation would need an explicit identity migration.

Local capture paths use `videos/users/<uid>/`; Supabase keys use `users/<uid>/processed/<recording_id>/<actual-output-name>`. Upload workers read the owner saved at capture, never the current Streamlit session. Firestore includes both ownership fields. The local/cloud video library and video API restrict non-admin results to the recorded owner; administrators retain their existing catalogue access. Ownership-free legacy videos are not automatically reassigned and require evidence-based migration before upload. Existing cloud objects were not moved or rewritten. Storage policies and Firestore rules were not deployed; use private buckets/server-authorized playback for account-restricted access, since public object URLs are public regardless of ownership metadata.

Ownership/authentication/pipeline regression run: 44 tests passed. Additional ownership tests cover local account directories, API denial of other users' videos and owner preservation during Storage inventory reconciliation.
`tests/test_video_ownership.py` plus `tests/test_supabase_database_sync.py`: **17 passed** after updating the legacy-account fake to return a real account mapping. Syntax and diff checks passed.
