import pandas as pd


def format_duration(seconds):
    seconds = int(seconds or 0)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def videos_dataframe(videos):
    rows = []
    for video in videos:
        summary = video.get("objects_summary", {})
        unique_ids = summary.get("unique_tracking_ids")
        if unique_ids is None:
            unique_ids = sorted(
                {
                    item.get("tracking_id")
                    for detection in video.get("detections", [])
                    for item in detection.get("objects", [])
                    if item.get("tracking_id") is not None
                }
            )
        rows.append(
            {
                "Filename": video.get("filename"),
                "Started at": video.get("started_at", "")[:19].replace("T", " "),
                "Duration (seconds)": round(video.get("duration_seconds", 0), 1),
                "Max people": summary.get("people_count_max", 0),
                "Max vehicles": summary.get("vehicle_count_max", 0),
                "Detection events": len(video.get("detections", [])),
                "Unique tracked objects": len(unique_ids),
                "Plates detected": ", ".join(
                    summary.get("plates_detected", [])
                ),
                "Movement events": summary.get("movement_events", 0),
                "Sync status": video.get("sync_status", "Not synced"),
                "Processing status": video.get(
                    "processing_status", "Not processed"
                ),
            }
        )
    return pd.DataFrame(rows)
