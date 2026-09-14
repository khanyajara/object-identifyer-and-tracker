# Supabase setup

1. Open the intended Supabase project → SQL Editor → New query.
2. Paste and run `supabase/migrations/20260914_roadwatch.sql` in full. It creates two application tables and a private `roadwatch-assets` bucket. It does not delete records or change the existing `videos` bucket.
3. From the project directory run `venv\Scripts\python.exe scripts/check_supabase.py`. Both table checks and the bucket check must pass.
4. Run `venv\Scripts\python.exe scripts/activate_supabase.py`. This checks prerequisites, copies existing local account hashes without overwriting conflicting accounts, and sets `ROADWATCH_SUPABASE_DATABASE_ENABLED=true` in `.env` only after success.
5. Restart the app. Administrator sign-in and privacy consent start a background sync pass every 30 seconds. Failed records remain local and retry; completed content hashes persist under `data/cloud_sync/`.

Keep `SUPABASE_SECRET_KEY` or legacy `SUPABASE_SERVICE_ROLE_KEY` exclusively on the server. The existing `SUPABASE_URL` must identify the project in which the SQL ran. Do not put privileged keys in frontend/VITE configuration. No anonymous database access is granted.

## Data destinations

| Data | Destination |
| --- | --- |
| New user accounts and bcrypt hashes | `roadwatch_accounts`, backend only |
| Video/session metadata, detection events, incidents, GPS, contacts, vehicle profiles, missing-person and stolen-vehicle reports, aggregate fatigue events, notification history, upload status | `roadwatch_records`, keyed by persistent device ID and local record path |
| Report images/PDFs, snapshots, thumbnails, CSV/PDF exports | Private `roadwatch-assets` Storage bucket, with object references in `roadwatch_records` |
| Compressed processed video | Existing private `videos` Storage bucket; signed playback URLs |

`roadwatch_records.payload` retains each source JSON document (including arrays). JSONL detection files are stored in batches of 100 events, with a manifest giving the authoritative chunk count. This is a cloud copy of device records, not a new per-user relational permission system. Admin pages continue to work from local records; the cloud video catalogue reads Supabase when enabled.

The existing application username/password flow and JWT remain in use. These accounts are **not** Supabase Auth users. The tables are intentionally inaccessible to `anon` and `authenticated`; only the trusted device backend accesses them. Public users still cannot access device records. Cloud account outages do not silently create a second local account.

Original raw recordings, biometric embeddings, model binaries, landmark samples, calibration data, administrator credentials and notification settings are not included in general record sync. Existing driver biometric storage is unchanged. Local file deletion does not erase archived cloud records or objects; retention/deletion needs an explicit policy. Large video uploads still use the existing standard upload path and project-wide Storage limits still apply.

Before activation, local account mode and the legacy Firebase metadata path remain available. After activation, new video metadata uses Supabase. Existing Firebase-only records are not automatically imported; records that still exist locally are copied by the background worker.

Passwords require eight characters without composition rules; the bcrypt input limit remains 72 UTF-8 bytes.

References: [Supabase private storage access](https://supabase.com/docs/guides/storage/security/access-control), [standard uploads](https://supabase.com/docs/guides/storage/uploads/standard-uploads).
