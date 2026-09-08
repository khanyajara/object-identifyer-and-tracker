"""Private SFace embedding computation; callers publish only confirmed sessions."""
import cv2
import numpy as np
from .face_detection_service import FaceDetectionService


def normalize_embedding(value):
    try:
        vector = np.asarray(value, dtype=np.float32).reshape(-1)
        norm = float(np.linalg.norm(vector))
        if vector.size != 128 or not np.isfinite(vector).all() or not np.isfinite(norm) or norm < 1e-8:
            raise ValueError()
        return vector / norm
    except (TypeError, ValueError, OverflowError):
        raise ValueError("Invalid SFace embedding.") from None


class FaceRecognitionService:
    def __init__(self, config, model=None):
        self.config, self._model = config, model
        self._attempted = model is not None
        self.error = None

    def embedding(self, frame, face):
        if not self._attempted:
            self._attempted = True
            try:
                if not self.config.sface_path.is_file():
                    raise FileNotFoundError()
                self._model = cv2.FaceRecognizerSF.create(str(self.config.sface_path), "", cv2.dnn.DNN_BACKEND_OPENCV, cv2.dnn.DNN_TARGET_CPU)
            except Exception:
                self.error = "Driver recognition model unavailable."
        if self._model is None:
            raise RuntimeError("Driver recognition model unavailable.")
        aligned = self._model.alignCrop(frame, FaceDetectionService.alignment_row(face))
        return normalize_embedding(self._model.feature(aligned))

    def recognize(self, frame, face, profiles):
        if not self.config.recognition_enabled or self.config.threshold is None:
            return None
        query = self.embedding(frame, face)
        best = None
        runner_up = -1.0
        for profile in profiles:
            if not profile.get("recognition_enabled") or profile.get("embedding_version") != "sface-v1":
                continue
            try:
                score = float(np.clip(query @ normalize_embedding(profile.get("embedding")), -1, 1))
            except ValueError:
                continue
            if best is None or score > best["confidence"]:
                runner_up = best["confidence"] if best else runner_up
                best = {"driver_id": profile["driver_id"], "display_name": profile.get("display_name", profile["driver_id"]), "confidence": score}
            else:
                runner_up = max(runner_up, score)
        if best and best["confidence"] >= self.config.threshold and best["confidence"] - runner_up >= .05:
            return best
        return None
