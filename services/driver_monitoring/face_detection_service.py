"""YuNet CPU adapter with a calibrated driver-seat region and ambiguity rejection."""
import cv2
import numpy as np


class FaceDetectionService:
    def __init__(self, config, detector=None):
        self.config = config
        self._model = detector
        self._attempted = detector is not None
        self.error = None

    def _load(self):
        if not self._attempted:
            self._attempted = True
            try:
                if not self.config.yunet_path.is_file():
                    raise FileNotFoundError()
                self._model = cv2.FaceDetectorYN.create(
                    str(self.config.yunet_path), "", (self.config.width, self.config.height),
                    0.85, 0.3, 100, cv2.dnn.DNN_BACKEND_OPENCV, cv2.dnn.DNN_TARGET_CPU,
                )
            except Exception:
                self.error = "Driver face detection model unavailable."
        return self._model

    def detect(self, frame):
        height, width = frame.shape[:2]
        result = dict(detected=False, confidence=0.0, bbox=None, landmarks=[],
                      frame_width=width, frame_height=height, faces=[], ambiguous=False)
        model = self._load()
        if model is None:
            return result
        try:
            small = cv2.resize(frame, (self.config.width, self.config.height))
            model.setInputSize((self.config.width, self.config.height))
            _, rows = model.detect(small)
            choices = []
            for raw in ([] if rows is None else rows):
                row = np.array(raw, dtype=np.float32).reshape(-1).copy()
                if row.size != 15 or not np.isfinite(row).all() or row[2] <= 0 or row[3] <= 0 or row[14] < .85:
                    continue
                row[:14:2] *= width / self.config.width
                row[1:14:2] *= height / self.config.height
                box = row[:4].tolist()
                face = dict(confidence=float(row[14]), bbox=box, landmarks=row[4:14].reshape(5, 2).tolist())
                result["faces"].append(face)
                cx, cy = (row[0] + row[2] / 2) / width, (row[1] + row[3] / 2) / height
                distance = float(np.hypot(cx - self.config.anchor_x, cy - self.config.anchor_y))
                if distance <= self.config.seat_radius:
                    score = float(row[2] * row[3] / (width * height)) * row[14] / (0.15 + distance)
                    choices.append((score, face))
            choices.sort(key=lambda item: item[0], reverse=True)
            if choices and len(choices) > 1 and choices[1][0] >= choices[0][0] * .8:
                result["ambiguous"] = True
            elif choices:
                result.update(choices[0][1], detected=True)
            self.error = None
        except Exception:
            self.error = "Driver face detection unavailable."
        return result

    @staticmethod
    def alignment_row(face):
        return np.asarray([*face["bbox"], *np.asarray(face["landmarks"]).reshape(-1), face["confidence"]], dtype=np.float32)
