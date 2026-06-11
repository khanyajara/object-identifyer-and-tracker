# Roadwatch Vision Recorder

Standalone Streamlit camera and AI recorder for the existing dashcam platform.

## Run

```powershell
.\venv\Scripts\activate
pip install -r requirements.txt
streamlit run streamlit_app.py
```

Open `http://localhost:8501`, select **Live Recorder**, and click
**Start Recording**. OpenCV opens the local webcam and Streamlit displays the
latest frame inside the page.

## Architecture

```text
OpenCV camera thread
  -> write every original high-resolution frame
  -> update the latest frame reference
Streamlit preview (maximum 5 FPS)
  -> display the latest annotated frame with st.image
AI sampler
  -> resize a copy to 640 x 360
  -> queue(maxsize=1), replacing stale samples
  -> YOLO / Deep SORT / OCR worker
  -> buffered logs linked to the current video
```

Recording does not wait for AI. If inference is busy, stale AI samples are
dropped while original camera frames continue to be written.

## Pages

1. Live Recorder
2. Recorded Videos
3. Detection Logs
4. Reports
5. Settings

Every detection is stored under its source video. Existing recordings in
`data/videos` and metadata in `data/logs` appear automatically.

## Default detection configuration

- OpenCV camera: HD 1280 x 720 at 30 FPS
- Full HD 1920 x 1080 is available from Settings when supported
- AI sampling: twice per second
- YOLO size: 416
- Object, vehicle, and person detection: on
- Deep SORT tracking: on
- Movement detection: on
- Plate OCR: on and throttled to detected vehicles / OCR interval
- Detection logging and metadata storage: on
- Video recording and saved-video analysis: on
- Snapshots: off
- Detection logs: batched every five seconds

The original camera frame is written before AI sampling. AI, tracking, OCR,
and logging failures do not stop recording. OCR is enabled but does not run on
every frame.

## Optional API

```powershell
uvicorn api_app:app --reload --port 8000
```
