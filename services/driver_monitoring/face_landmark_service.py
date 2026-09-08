"""Stable measurement API for future temporal fatigue scoring; no classifier."""
import math
import cv2
import numpy as np


def empty_measurements():
    return {"face_visible": False, "eyes": {}, "mouth": {}, "head_pose": {}, "quality": {"available": False}}


class FaceLandmarkService:
    def __init__(self, config):
        self.config, self._model = config, None
        self._attempted, self._last_ms = False, -1
        self.error = None

    def extract(self, frame, face, now):
        if not self._attempted:
            self._attempted = True
            try:
                if not self.config.landmarker_path.is_file():
                    raise FileNotFoundError()
                import mediapipe as mp
                self._mp = mp
                options = mp.tasks.vision.FaceLandmarkerOptions(
                    base_options=mp.tasks.BaseOptions(model_asset_path=str(self.config.landmarker_path), delegate=mp.tasks.BaseOptions.Delegate.CPU),
                    running_mode=mp.tasks.vision.RunningMode.VIDEO, num_faces=1,
                    output_facial_transformation_matrixes=True,
                )
                self._model = mp.tasks.vision.FaceLandmarker.create_from_options(options)
            except Exception:
                self.error = "Driver landmark model unavailable."
        if self._model is None:
            return empty_measurements()
        try:
            # Crop only the selected driver; passengers cannot supply fatigue landmarks.
            x, y, w, h = face["bbox"]
            fh, fw = frame.shape[:2]
            x0, y0 = max(0, int(x - .15*w)), max(0, int(y - .15*h))
            x1, y1 = min(fw, int(x + 1.15*w)), min(fh, int(y + 1.15*h))
            crop = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2RGB)
            stamp = max(self._last_ms + 1, int(now * 1000))
            self._last_ms = stamp
            result = self._model.detect_for_video(self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=np.ascontiguousarray(crop)), stamp)
            if not result.face_landmarks:
                return empty_measurements()
            points = result.face_landmarks[0]
            xy = np.asarray([(p.x * (x1-x0), p.y * (y1-y0)) for p in points])
            def ratio(a, b, c, d):
                return float(np.linalg.norm(xy[a]-xy[b]) / max(1e-6, np.linalg.norm(xy[c]-xy[d])))
            eyes = {"left_opening_ratio": (ratio(160,144,33,133)+ratio(158,153,33,133))/2,
                    "right_opening_ratio": (ratio(385,380,362,263)+ratio(387,373,362,263))/2}
            pose = {}
            if len(result.facial_transformation_matrixes):
                rotation = np.asarray(result.facial_transformation_matrixes[0])[:3,:3]
                rotation = rotation / np.linalg.norm(rotation, axis=0)
                pitch = math.atan2(rotation[2,1], rotation[2,2])
                yaw = math.atan2(-rotation[2,0], math.hypot(rotation[0,0], rotation[1,0]))
                roll = math.atan2(rotation[1,0], rotation[0,0])
                pose = dict(pitch=math.degrees(pitch), yaw=math.degrees(yaw), roll=math.degrees(roll), units="degrees", calibrated=False)
            self.error = None
            return dict(face_visible=True, eyes=eyes, mouth={"opening_ratio": ratio(13,14,78,308)},
                        head_pose=pose, quality={"available": True, "landmark_count": len(points)}, sampled_at_monotonic=now)
        except Exception:
            self.error = "Driver landmarks unavailable."
            return empty_measurements()

    def close(self):
        if self._model is not None:
            self._model.close()
            self._model = None
