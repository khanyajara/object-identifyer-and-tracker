class ObjectTracker:
    def __init__(self):
        self.backend = "Deep SORT"
        try:
            from deep_sort_realtime.deepsort_tracker import DeepSort

            self.tracker = DeepSort(max_age=15, n_init=1)
        except Exception:
            self.tracker = None
            self.backend = "Disabled"

    def update(self, detections, frame):
        if self.tracker is None:
            return [{**item, "tracking_id": None} for item in detections]
        raw = [
            (
                [
                    item["box"]["x"],
                    item["box"]["y"],
                    item["box"]["width"],
                    item["box"]["height"],
                ],
                item["confidence"] / 100,
                item["label"],
            )
            for item in detections
        ]
        tracks = self.tracker.update_tracks(raw, frame=frame)
        objects = []
        for track in tracks:
            if track.time_since_update > 1:
                continue
            left, top, right, bottom = track.to_ltrb(orig=True)
            objects.append(
                {
                    "label": track.get_det_class() or "object",
                    "tracking_id": int(track.track_id),
                    "confidence": float(track.get_det_conf() or 0) * 100,
                    "box": {
                        "x": int(left),
                        "y": int(top),
                        "width": int(right - left),
                        "height": int(bottom - top),
                    },
                }
            )
        if objects:
            return objects
        return [{**item, "tracking_id": None} for item in detections]
