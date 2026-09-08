"""Multi-sample, in-memory enrollment; raw images are never persisted."""
import hashlib
import cv2
import numpy as np
from .biometric_store import require_admin
from .face_recognition_service import normalize_embedding


class FaceEnrollmentService:
    def __init__(self, detector, recognizer, store, minimum_samples=5):
        self.detector, self.recognizer, self.store = detector, recognizer, store
        self.minimum_samples = max(3, minimum_samples)
        self.cancel()

    def cancel(self):
        self._samples, self._poses, self._hashes = [], [], set()
        self._actor = self._driver_id = None
        self.status = {"status": "idle", "samples": 0, "message": ""}

    def start(self, actor, driver_id, now):
        require_admin(actor)
        if self.recognizer.config.threshold is None:
            raise ValueError("Calibrate FACE_RECOGNITION_THRESHOLD before enrollment.")
        self.cancel()
        self._actor, self._driver_id = dict(actor), driver_id
        self._started, self._last_sample = now, -float("inf")
        self.status = {"status": "capturing", "samples": 0, "message": "Look at the cabin camera and turn slightly between samples."}

    def process(self, frame, now):
        if self.status["status"] != "capturing":
            return
        if now - self._started > 60:
            self.cancel()
            self.status.update(status="failed", message="Enrollment timed out; capture five clear samples with slight pose variation.")
            return
        if frame is None or now - self._last_sample < .75:
            return
        self._last_sample = now
        try:
            face = self.detector.detect(frame)
            if not face["detected"] or len(face["faces"]) != 1:
                raise ValueError("Exactly one face must be visible in the driver seat.")
            x, y, w, h = map(int, face["bbox"])
            fh, fw = frame.shape[:2]
            if min(w, h) < 80 or x < 0 or y < 0 or x + w > fw or y + h > fh:
                raise ValueError("Move closer and keep your whole face in view.")
            crop = cv2.cvtColor(frame[y:y+h, x:x+w], cv2.COLOR_BGR2GRAY)
            if not 40 < float(crop.mean()) < 220:
                raise ValueError("Improve the lighting on your face.")
            if cv2.Laplacian(crop, cv2.CV_64F).var() < 60:
                raise ValueError("Hold still briefly; the face image is blurred.")
            points = np.asarray(face["landmarks"])
            if points.shape != (5, 2) or not np.isfinite(points).all():
                raise ValueError("Eyes and facial landmarks must be visible.")
            digest = hashlib.sha256(crop.tobytes()).digest()
            if digest in self._hashes:
                raise ValueError("Waiting for a new camera sample.")
            embedding = self.recognizer.embedding(frame, face)
            threshold = self.recognizer.config.threshold
            if any(float(embedding @ sample) < threshold for sample in self._samples):
                raise ValueError("Samples are inconsistent. Keep the same driver in view.")
            self._hashes.add(digest)
            self._samples.append(embedding)
            self._poses.append(float((points[2, 0] - x) / w))
            self.status.update(samples=len(self._samples), message="Sample accepted. Turn your head slightly.")
            if len(self._samples) >= self.minimum_samples:
                if max(self._poses) - min(self._poses) < .025:
                    self._samples.pop(0)
                    self._poses.pop(0)
                    self.status.update(samples=len(self._samples), message="Slight natural head turn needed.")
                    return
                result = self.store.save_enrollment(self._actor, self._driver_id, normalize_embedding(np.mean(self._samples, axis=0)))
                self.cancel()
                self.status.update(status="complete", samples=self.minimum_samples, message="Enrollment complete.", driver_id=result["driver_id"])
        except ValueError as exc:
            self.status["message"] = str(exc)
        except Exception:
            self.cancel()
            self.status.update(status="failed", message="Enrollment unavailable; check models and private database configuration.")
