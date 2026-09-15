import time
from collections import Counter

from core.annotation import annotate_frame
from core.detector import ObjectDetector
from core.movement import MovementDetector
from core.ocr import PlateScanner
from core.tracker import ObjectTracker


VEHICLES = {"car", "truck", "bus", "motorcycle", "bicycle"}


class VisionPipeline:
    def __init__(
        self, model_name, confidence, image_size, enable_tracking=True,
        enable_ocr=False, ocr_interval_seconds=5, model=None, ocr_reader=None
    ):
        self.detector = ObjectDetector(
            model_name, confidence, image_size, model=model
        )
        self.tracker = ObjectTracker() if enable_tracking else None
        self._camera_trackers = {}
        self.movement = MovementDetector()
        self.plate_scanner = PlateScanner(ocr_reader) if enable_ocr else None
        self.ocr_interval_seconds = ocr_interval_seconds
        self.last_ocr = 0.0

    @property
    def capabilities(self):
        return {
            "detector": "YOLOv8",
            "tracker": self.tracker.backend if self.tracker else "Disabled",
            "ocr": self.plate_scanner.backend if self.plate_scanner else "Disabled",
        }

    def process(
        self, frame, frame_number, camera_fps,
        media_timestamp_seconds=None, camera_role=None
    ):
        detections = self.detector.detect(frame)
        tracker = self.tracker
        if tracker and camera_role is not None:
            if camera_role not in self._camera_trackers:
                self._camera_trackers[camera_role] = ObjectTracker()
            tracker = self._camera_trackers[camera_role]
        objects = (
            tracker.update(detections, frame)
            if tracker
            else [{**item, "tracking_id": None} for item in detections]
        )
        movement, score = self.movement.detect(frame)
        vehicles = [item for item in objects if item["label"] in VEHICLES]
        plates = []
        now = (
            media_timestamp_seconds
            if media_timestamp_seconds is not None
            else time.monotonic()
        )
        if (
            self.plate_scanner
            and vehicles
            and now - self.last_ocr >= self.ocr_interval_seconds
        ):
            plates = self.plate_scanner.scan(frame, vehicles)
            self.last_ocr = now
        counts = Counter(item["label"] for item in objects)
        event = {
            "frame_number": frame_number,
            "movement_detected": movement,
            "movement_score": score,
            "people_count": counts.get("person", 0),
            "vehicle_count": sum(counts.get(label, 0) for label in VEHICLES),
            "plate_text": next(
                (plate["text"] for plate in plates if plate.get("text")), None
            ),
            "objects": objects,
            "plates": plates,
            "stolen_match": False,
        }
        return annotate_frame(
            frame.copy(), objects, plates, movement, camera_fps
        ), event
