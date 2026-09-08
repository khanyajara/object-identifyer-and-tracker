# Roadwatch Vision Recorder

Roadwatch Vision Recorder is a standalone Python Streamlit app that turns a local webcam into a dashcam-style AI recorder. It records smooth original camera video, runs object detection and tracking, scans plates when possible, saves detection metadata under each recording, and generates an annotated processed video after recording stops.

The app is designed to work beside an existing dashcam platform. The existing platform can stay as the main dashboard, while this Python app handles camera capture, AI analysis, video storage, and metadata export/sync.

## What It Does

- Opens a local webcam with OpenCV.
- Displays the camera preview inside the Streamlit page.
- Records original high-resolution video frames.
- Keeps recording independent from AI processing.
- Runs YOLO object detection.
- Detects people, vehicles, and other COCO objects.
- Runs Deep SORT tracking when enabled.
- Runs movement detection.
- Runs OCR/plate scanning when enabled and useful.
- Saves every detection event under the active `video_id`.
- Saves captured objects, tracking IDs, confidence scores, bounding boxes, plates, and movement status.
- Post-processes the saved video after recording stops.
- Saves an annotated processed video with boxes, labels, IDs, confidence scores, plate text, and FPS.
- Shows saved recordings, detection summaries, detection timelines, and captured-object tables.
- Provides an optional FastAPI endpoint for future sync with another dashcam app.

## Main App Flow

```text
Start Recording
  -> create video_id
  -> open camera once
  -> start camera capture thread
  -> write original frames to WebM
  -> sample frames for live AI preview
  -> save live detection metadata

Stop Recording
  -> stop camera thread
  -> release camera and writer
  -> finalize original WebM
  -> run full post-processing over saved video
  -> write processed annotated WebM
  -> rebuild/save final detection metadata
  -> show recording in Recorded Videos
```

Recording quality is prioritized over AI speed. The camera thread writes frames first. AI, OCR, tracking, logging, Streamlit rendering, and post-processing must not block the capture loop.

## Video Files

Each recording keeps both the original and processed video when available.

```text
data/videos/
  recording_2026_07_01_143522.webm
  recording_2026_06_11_153024.json

data/videos/processed/
  recording_vid_20260701_143522_d9a878_processed.webm
```

Metadata stores paths like:

```json
{
  "video_id": "vid_20260701_143522_d9a878",
  "filename": "recording_2026_07_01_143522.webm",
  "video_format": "webm",
  "video_path": "data/videos/recording_2026_07_01_143522.webm",
  "original_video_path": "data/videos/recording_2026_07_01_143522.webm",
  "processed_video_path": "data/videos/processed/recording_vid_20260701_143522_d9a878_processed.webm",
  "processing_status": "Processed video saved successfully.",
  "detections": [],
  "objects_summary": {}
}
```

The Recorded Videos page defaults to the processed annotated video. If there is no processed video, it falls back to the original. New recordings use WebM. Older MP4 recordings remain supported and playable during the transition.

## Detection Metadata

Every detection belongs to a single video. The relationship is:

```text
Video
  -> Detection Events
      -> Objects
      -> Plates
      -> Movement Status
      -> Snapshots, when enabled
```

Example detection event:

```json
{
  "video_id": "vid_20260611_153024_d9a878",
  "timestamp": "2026-06-11T13:30:24+00:00",
  "frame_number": 120,
  "movement_detected": true,
  "people_count": 1,
  "vehicle_count": 2,
  "plate_text": "CA123456",
  "tracking_ids": [1, 2],
  "objects": [
    {
      "video_id": "vid_20260611_153024_d9a878",
      "label": "car",
      "tracking_id": 2,
      "confidence": 91.4,
      "box": {
        "x": 100,
        "y": 80,
        "width": 300,
        "height": 200
      }
    }
  ],
  "plates": [
    {
      "text": "CA123456",
      "confidence": 88.0,
      "box": {
        "x": 100,
        "y": 80,
        "width": 300,
        "height": 200
      }
    }
  ]
}
```

## Sal Dashcam-Style Pages

### Dash Cam

The main camera page.

- Start and stop recording.
- Shows large in-page OpenCV camera preview with `st.image`.
- Shows camera FPS, AI status, processing time, recording timer, object count, people count, vehicle count, movement status, plate text, latest AI scan result, storage status, GPS status, speed, and SOS UI.
- Stores `CameraManager` in Streamlit session state so normal UI refreshes do not reopen the webcam.

### Videos

Saved evidence library.

- Search by filename, object, plate, or date.
- Filter by processed videos, original only, plates, movement, people, vehicles, or possible incidents.
- Shows a summary table of recordings.
- Plays processed video by default when available.
- Offers an Original/Processed selector.
- Shows detection summary.
- Shows object counts.
- Shows plates and movement events.
- Shows full detection timeline.
- Shows Captured Objects table for the selected video only.
- Warns when metadata exists but the WebM or MP4 file is missing.

### Incidents

Local incident workflow generated from Roadwatch metadata.

- Movement detected
- Person detected
- Vehicle detected
- Plate detected
- Possible stolen vehicle match
- AI processing error
- Manual incident

Incidents can be filtered by severity, type, and status. They can be marked reviewed or dismissed.

### Stolen Vehicle

Local stolen vehicle watchlist.

- Add plate number, make, model, colour, contact note, case/reference, and status.
- Search reported plates.
- Compare detected OCR plates against the local stolen vehicle list.
- Create high severity incidents for possible stolen matches.

### GPS History

Desktop-safe GPS page with local/mock support.

- Shows GPS status.
- Shows latest latitude/longitude and speed when available.
- Stores GPS history locally.
- Can link mock GPS points to a video.

### Emergency Contacts

Local SOS contact management.

- Add contacts.
- Edit contact data.
- Delete contacts.
- Toggle active for SOS.
- Shows how many contacts would receive SOS when a notification provider is configured.

### Vehicle Profile

Local vehicle profile used by future exports, incidents, and reports.

- Make
- Model
- Year
- Colour
- Registration/plate number
- Driver name
- Company/fleet name

### Analytics

Shows aggregate stats across saved recordings.

- Total recordings
- Processed videos
- Incidents
- High severity incidents
- Objects detected
- People detected
- Vehicles detected
- Plates detected
- Movement events
- Processing errors
- Average recording duration
- Charts for objects, incidents, recordings, plates, and movement

### Settings

Sal-style grouped settings:

- Camera Settings
- AI Settings
- Storage Settings
- Sync/API Settings
- Supabase placeholders

## Architecture

```text
Streamlit UI
  -> Live Recorder controls
  -> preview placeholder
  -> stats and saved-video pages

CameraManager
  -> opens OpenCV camera once
  -> reads frames in background thread
  -> writes original frames to video
  -> stores latest frame for UI
  -> samples frames into AI queue

VisionPipeline
  -> YOLO detection
  -> Deep SORT tracking
  -> movement detection
  -> OCR/plate scanning
  -> frame annotation

VideoProcessingService
  -> reads finalized original WebM or legacy MP4
  -> runs full AI pass frame by frame
  -> writes processed annotated WebM
  -> saves final detection timeline

Services
  -> video metadata
  -> detection logs
  -> reports
  -> optional sync
```

## Project Structure

```text
Object-Detection-and-Tracking/
  streamlit_app.py
  main.py
  api_app.py
  requirements.txt
  .env.example
  settings.json

  core/
    annotation.py
    detector.py
    movement.py
    ocr.py
    opencv_recorder.py
    tracker.py
    video_io.py
    vision_pipeline.py

  services/
    contact_service.py
    detection_log_service.py
    gps_service.py
    incident_service.py
    local_json_service.py
    report_service.py
    storage_service.py
    stolen_vehicle_service.py
    supabase_service.py
    sync_service.py
    vehicle_profile_service.py
    video_processing_service.py
    video_service.py

  data/
    videos/
      processed/
    logs/
    snapshots/
    exports/
    incidents/
    stolen_vehicles/
    gps/
    contacts/
    profile/

  models/
    yolov8n.pt
```

## Installation

Create and activate a virtual environment:

```powershell
python -m venv venv
.\venv\Scripts\activate
```

Install dependencies:

```powershell
pip install -r requirements.txt
```

Run the Streamlit app:

```powershell
streamlit run streamlit_app.py
```

Open:

```text
http://localhost:8501
```

You can also run through `main.py` because it forwards to `streamlit_app.py`:

```powershell
streamlit run main.py
```

## Optional API

Run the FastAPI app:

```powershell
uvicorn api_app:app --reload --port 8000
```

Available endpoints:

```text
GET  /health
GET  /videos
GET  /videos/{video_id}
POST /videos/sync
```

## Default Settings

Current defaults are aimed at smooth camera recording with useful AI:

```text
CAMERA_INDEX=0
CAMERA_WIDTH=1280
CAMERA_HEIGHT=720
TARGET_CAMERA_FPS=30
CAMERA_BUFFER_SIZE=1

AI_FRAME_WIDTH=640
AI_FRAME_HEIGHT=360
AI_PROCESS_INTERVAL_SECONDS=0.5
YOLO_IMAGE_SIZE=416

ENABLE_TRACKING=true
ENABLE_OCR=true
SAVE_SNAPSHOTS=false
SAVE_ANNOTATED_VIDEO=true
STREAMLIT_PREVIEW_FPS=5
```

Camera recording and AI processing are separate:

```text
Camera recording: every frame
Live AI preview: sampled frames
Post-processing: full saved video after Stop
```

## Performance Notes

- Streamlit is not a high-FPS video renderer, so the preview is intentionally limited.
- Recording uses OpenCV in a background thread.
- The original WebM is written before any AI work.
- Live AI uses sampled frames to keep the webcam smooth.
- Full detection overlays are generated after recording stops.
- OCR is slower than object detection and is throttled.
- Deep SORT tracking can be CPU-heavy, especially on long videos.

## Video Playback Notes

The app now checks whether video files are actually present and readable before rendering the player.

If you see:

```text
Video file unavailable. The metadata exists, but the video is missing or unreadable on disk.
```

that means the JSON record exists in `data/logs`, but the actual WebM or MP4 file is missing, moved, empty, or unreadable. Restore the video to the expected path or record a new video.

For new recordings, the app writes browser-friendly WebM files using VP9 first and VP8 as a fallback. Existing MP4 files are still discovered, validated, and played.

## Sync With Existing Dashcam App

This app does not rebuild the existing dashcam platform. It prepares the data that platform can receive later:

- original video path or URL
- processed annotated video path or URL
- detection summary
- detection timeline
- captured objects
- object counts
- plate results
- movement events
- snapshots, if enabled

Configure the sync target in Settings:

```text
VISION_SYNC_URL=
VISION_SYNC_API_KEY=
```

Then use the Sync button on a saved video.

## Troubleshooting

### Camera does not open

- Check `CAMERA_INDEX`.
- Close Zoom, Teams, browser camera tabs, or any app using the webcam.
- Try camera index `1` or `2` in Settings.

### Preview is slow

- Lower `STREAMLIT_PREVIEW_FPS`.
- Increase `AI_PROCESS_INTERVAL_SECONDS`.
- Use a smaller `YOLO_IMAGE_SIZE`.
- Keep OCR and tracking enabled only if your machine can handle them.

### Recording is laggy

Recording should not wait for AI. If it still lags:

- Lower camera resolution.
- Close other camera apps.
- Use 1280x720 before trying 1920x1080.
- Keep the app on local disk if OneDrive file locking becomes a problem.

### Saved video does not play

- Open the Recorded Videos warning and check the listed paths.
- Confirm the WebM or MP4 exists under `data/videos` or `data/videos/processed`.
- Old metadata records may point to MP4s that were deleted or moved.
- Record a new sample after this update to verify the current playback flow.

### Post-processing fails

The original video is kept. The metadata stores `processing_error` so the issue can be inspected later.

Common causes:

- original video file missing
- invalid/empty WebM or MP4
- YOLO model missing
- codec writer unavailable
- insufficient disk space

## Current Role Of This Project

This project is the camera and AI recorder:

```text
Existing Dashcam App
  -> users
  -> admin
  -> reports
  -> saved videos
  -> alerts

Streamlit Vision Recorder
  -> webcam capture
  -> video recording
  -> object detection
  -> tracking
  -> plate scanning
  -> processed annotated videos
  -> detection metadata
  -> optional sync API
```

The important rule:

```text
Record every camera frame.
Process AI without blocking recording.
Save detections under the video they belong to.
Generate annotated saved video after recording stops.
```
# Driver monitoring

Roadwatch includes an optional, isolated YuNet/SFace/MediaPipe cabin subsystem.
See [driver monitoring setup, security, resource report and manual checklist](docs/driver_monitoring.md).
Recognition is off until the model files, private driver storage and calibrated
threshold are configured; road recording continues independently.

Driver fatigue now reuses background driver landmarks for temporal warnings and linked local incidents. See [fatigue monitoring and deployment validation](docs/fatigue_monitoring.md). Alerts require administrator calibration; native-camera acceptance remains pending.
