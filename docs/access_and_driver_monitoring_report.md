# Access changes and driver-monitoring test report

Date: 15 September 2026

## Summary

Standard users now enter the client workspace after sign-in and privacy consent. Administrator tools remain restricted to verified admin and super_admin roles. The current driver-monitoring regression run passed all 96 tests. Physical camera operation and real-world recognition/fatigue accuracy have not been validated by this work.

## User and administrator access

Previously, registered users reached an account-only screen and the video API rejected their role. Standard users can now access:

- Live camera and recording page.
- Videos and video read APIs.
- Missing-person and stolen-vehicle report submission.
- GPS tracking, emergency contacts and vehicle profiles.
- Account information and sign-out.

Administrators retain dashboard, report review, driver management, system settings and admin management access. Navigation derives from the verified token role; an admin URL parameter does not elevate a standard account. The separate admin login now rejects standard credentials. Self-registration continues to create only standard accounts. Video sync ingestion remains restricted to operators and administrators.

Admin Management now shows configured accounts and accurate sign-in/permission information. Administrator role changes still require server configuration through ROADWATCH_ADMIN_ACCOUNTS; an interactive role editor was not added. Videos remain part of the shared device workspace; per-user recording isolation was not introduced.

Changed files: streamlit_app.py, api_app.py, tests/test_user_registration.py and README.md.

## Access validation

The preceding access-change test runs completed with 16 passes and one skip:

- Seven registration/access tests passed, covering registration, password rules, sign-in, client navigation, report routing, video reads, sync denial, sign-out, admin URL handling and rejection of standard credentials by admin login.
- The authentication/API/support suite ran ten tests: nine passed, and the metadata-repair test was skipped because no local MP4 fixture was available.
- git diff --check passed.

Camera and background services were mocked in the navigation test. These results establish routing and permission behavior, not physical camera capture.

## Driver-monitoring regression testing

A fresh unittest run against test_driver*.py, test_face*.py, test_fatigue_detection.py and test_monitoring_hardening.py passed all 96 tests with no skips or failures.

Coverage includes:

- Face detection, driver-seat selection and ambiguous/missing faces.
- Recognition thresholds, invalid embeddings and unknown, disabled or deleted drivers.
- Administrator-only enrollment, sample quality and biometric storage boundaries.
- Repeated-match identity confirmation, mismatch handling and expiry.
- Background scheduling, bounded frame queues, camera reuse and recovery from camera or worker failures.
- Streamlit reruns, administrator navigation and standard-user exclusion from driver management.
- Driver identity links to recordings, incidents and GPS metadata.
- Fatigue logic for blinks, prolonged eye closure, yawns, head nods, stale samples, calibration, cooldowns and event persistence.

Expected failure-recovery log messages appeared during tests; the suite completed successfully.

## Previously saved stability evidence

These existing results were reviewed, not rerun for this report. Both used synthetic capture, models and database adapters, without native inference or network traffic.

| Measure | Driver monitoring | Fatigue enabled |
| --- | --- | --- |
| Duration | 180.51 seconds | 180.14 seconds |
| Result | Passed | Passed |
| Simulated reruns | 341 | 348 |
| Peak pending frame queue | 1 | 1 |
| Warm memory growth | 0.10 MiB | 0.04 MiB |
| Camera opens / releases | 1 / 1 | 1 / 1 |
| Fatigue event writes | Not applicable | 8 |

Sources: driver_monitoring_stability.json and fatigue_stability.json in this directory. These figures measure simulated orchestration stability, not real-camera performance or detection accuracy.

## Deployment readiness and remaining testing

The saved driver_deployment_validation.json reports that the YuNet, SFace and face-landmarker assets loaded successfully, and YuNet processed a test frame. It also reports that recognition, eye and mouth thresholds and neutral head pitch were not configured. Its camera-acceptance readiness flag is false.

Outstanding validation:

1. Calibrate recognition and fatigue thresholds using consented driver samples.
2. Verify the physical cabin camera, positioning, lighting and disconnect/reconnect behavior.
3. Measure false matches, missed matches and fatigue false alerts with representative data.
4. Validate cloud access rules and authorized driver enrollment end to end.
5. Measure native-model performance alongside road detection, OCR and recording over a representative trip.

Conclusion: client access and automated driver-monitoring behavior pass their tested checks. Real-device acceptance and accuracy validation remain pending.
