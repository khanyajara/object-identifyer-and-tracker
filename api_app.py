from fastapi import FastAPI, HTTPException

from services.video_service import VideoService


app = FastAPI(title="Roadwatch Vision Recorder API")
service = VideoService()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/videos")
def videos():
    return service.list_videos()


@app.get("/videos/{video_id}")
def video(video_id: str):
    try:
        return service.load(video_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/videos/sync")
def receive_sync(payload: dict):
    return {"status": "received", "video_id": payload.get("video_id")}
