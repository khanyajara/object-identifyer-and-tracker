from core.detection_policy import box_iou, suppress_duplicates


class ObjectTracker:
    def __init__(self):
        self._fallback_tracks = {}
        self._next_id = 1
        self.backend = "Deep SORT"
        try:
            from deep_sort_realtime.deepsort_tracker import DeepSort

            self.tracker = DeepSort(max_age=15, n_init=1)
        except Exception:
            self.tracker = None
            self.backend = "IoU fallback"

    def _fallback_update(self, detections):
        # One-to-one matching retains IDs over short gaps without inventing boxes.
        for track in self._fallback_tracks.values():
            track['age'] += 1
        self._fallback_tracks = {
            key: track for key, track in self._fallback_tracks.items() if track['age'] <= 15
        }
        candidates = sorted(
            ((box_iou(item['box'], track['item']['box']), index, key)
             for index, item in enumerate(detections)
             for key, track in self._fallback_tracks.items()
             if item['label'] == track['item']['label']),
            reverse=True,
        )
        matches, used = {}, set()
        for overlap, index, key in candidates:
            if overlap >= 0.3 and index not in matches and key not in used:
                matches[index] = key
                used.add(key)
        objects = []
        for index, item in enumerate(detections):
            key = matches.get(index)
            if key is None:
                key = self._next_id
                self._next_id += 1
            self._fallback_tracks[key] = {'item': item, 'age': 0}
            objects.append({**item, 'tracking_id': key})
        return objects

    def update(self, detections, frame):
        detections = suppress_duplicates(detections)
        if self.tracker is None:
            return self._fallback_update(detections)
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
            if track.time_since_update != 0:
                continue
            bounds = track.to_ltrb(orig=True, orig_strict=True)
            if bounds is None:
                continue
            left, top, right, bottom = bounds
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
        return suppress_duplicates(objects)
