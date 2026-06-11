from pathlib import Path

from ultralytics import YOLO


def load_yolo_model(model_name):
    local = Path(model_name)
    if not local.exists():
        local = Path(__file__).resolve().parents[1] / model_name
    return YOLO(str(local))


class ObjectDetector:
    def __init__(self, model_name, confidence, image_size, model=None):
        self.model = model or load_yolo_model(model_name)
        self.confidence = confidence
        self.image_size = image_size

    def detect(self, frame):
        result = self.model.predict(
            frame,
            conf=self.confidence,
            imgsz=self.image_size,
            verbose=False,
        )[0]
        detections = []
        for box in result.boxes:
            x1, y1, x2, y2 = box.xyxy[0].cpu().tolist()
            detections.append(
                {
                    "label": self.model.names[int(box.cls[0])],
                    "confidence": float(box.conf[0]) * 100,
                    "box": {
                        "x": int(x1),
                        "y": int(y1),
                        "width": int(x2 - x1),
                        "height": int(y2 - y1),
                    },
                }
            )
        return detections
