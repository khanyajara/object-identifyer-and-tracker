# Local security review — 2026-09-14

This is a focused source review and regression check, not a penetration test or certification. No public deployment or honeypot was created. Live cloud policies, network exposure, dependency vulnerabilities and historical secret leakage remain unverified.

## Changes

- Self-registration creates a fixed `user` role backed by a local SQLite database with unique normalized usernames and bcrypt hashes. It grants only an account page. Shared device data and APIs remain restricted. Registration shares the process login budget. Account storage must be protected by host filesystem permissions; email verification, account recovery and stream assignment are not implemented.

- Require a signed, expiring administrator token before initializing cameras, GPS, services or device-console pages. Viewer/operator API roles do not grant device-console access.
- Require JWT expiry, issued-at, issuer, subject and role claims. Reject tokens whose account was removed or whose role changed. Sign-out removes the session token. Preview refresh checks expiry.
- Limit login attempts across the UI and API to 60 per minute per process. This bounded budget is an initial local control; a shared edge limiter and MFA are required for internet deployment. A global budget can be exhausted by an attacker, and multiple processes have independent budgets.
- Bind Streamlit to loopback by default. Explicitly enable CORS/XSRF safeguards and disable static file serving. Command-line/environment overrides can change these defaults.
- Restrict cached/generated playback links to the configured HTTPS storage origin; disallow diagnostic redirects to another destination.

## Findings that still block public deployment

1. The console owns cameras, recordings, contacts, GPS and administrative credentials. Build a separate viewer service with explicit per-stream authorization and short-lived access. Do not publish this device console as a public streaming page.
2. Playback now restricts URLs and redirects, but configured storage hosts remain trusted administrator input. Add network egress controls for public hosting and verify DNS/network behavior in deployment.
3. Deployed Firestore rules and storage bucket visibility were not inspected. The biometric rules file is only a snippet. Server SDK access requires restricted service-account permissions; client rules do not constrain privileged server credentials.
4. Git already tracks some contacts, GPS, incident, vehicle-profile and recording metadata. Ignore rules do not untrack existing files or erase history. Review these records privately before publishing the repository; rotate any exposed credentials. `.env` and Streamlit secrets were not listed as tracked in the current index, but history was not scanned.
5. Dependency versions are pinned in the main requirements file, but this review did not obtain an advisory-backed dependency audit or generate a full transitive lockfile.
6. Sessions use the existing symmetric signing secret. Password changes do not individually revoke issued tokens; account removal, role changes, expiry or secret rotation do. Existing capture workers may continue recording after sign-out; access is revoked, but capture is device-owned.
7. Native inference can delay preview shutdown/recording transitions while an in-flight batch completes. Preview rendering itself does not wait for inference. Hardware throughput remains unmeasured.

## Honeypot design

Yes, a honeypot can be added as a detection system. It does not prevent compromise. Use a separate disposable VM/network segment with no route to the recorder, private cameras, biometric database, cloud credentials or evidence storage. Deny outbound connections by default. Forward limited logs to a protected collector with retention and alert limits.

Start with a low-interaction HTTP decoy containing only invented data. Monitor unexpected probes and repeated authentication attempts. Do not deploy intentionally vulnerable software on the recorder, use real credentials as bait, or automatically retaliate/block users based solely on a decoy hit. Choose hosting and alert destinations before deploying it.

## Future streaming boundary

Camera capture → authenticated media gateway → authorized viewers over TLS. Keep the admin console reachable only over a private network/VPN. Use a real media transport such as WebRTC for low-latency delivery after benchmarking; the current 15 FPS Streamlit image preview is a local target, not a multi-viewer streaming service or measured FPS guarantee.

References: [OWASP authentication](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html), [Streamlit fragments](https://docs.streamlit.io/develop/api-reference/execution-flow/st.fragment).
