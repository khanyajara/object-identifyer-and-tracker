"""Preserve live identity when road video is reprocessed offline."""
from bisect import bisect_right


def preserve_event_identity(events, live_events, fps):
    from .runtime import identity_metadata
    samples = sorted((item for item in live_events if "driver_identity_status" in item and isinstance(item.get("frame_number"), (int, float))), key=lambda item: item["frame_number"])
    frames = [item["frame_number"] for item in samples]
    for event in events:
        number = event.get("frame_number", 0)
        index = bisect_right(frames, number) - 1
        # Only carry a recent preceding live observation, never the current driver
        # or a future sample from later in this video.
        context = {"driver_id": None, "driver_identity_status": "unknown", "driver_session_id": None}
        if index >= 0 and frames[index] <= number <= samples[index].get("end_frame", frames[index]) + max(1, fps):
            context = identity_metadata(samples[index])
        event.update(context)
