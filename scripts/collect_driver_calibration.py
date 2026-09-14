"""Stationary, local camera calibration. Stores scalar measurements, never images."""
import argparse
import csv
import time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.calibrate_driver_fatigue import calibrate


def sample_value(label, measurements):
    import math
    if not measurements.get("face_visible") or not measurements.get("quality", {}).get("available"):
        return None
    try:
        pose = measurements["head_pose"]
        if abs(pose["yaw"]) > 20 or abs(pose["roll"]) > 20:
            return None
        if label in {"eyes_closed", "eyes_open"}:
            eyes = measurements["eyes"]
            value = max(eyes["left_opening_ratio"], eyes["right_opening_ratio"])
        elif label in {"mouth_rest", "yawn"}:
            value = measurements["mouth"]["opening_ratio"]
        elif label == "neutral_pitch":
            value = pose["pitch"]
        else:
            raise ValueError("Unknown calibration label")
        return float(value) if math.isfinite(value) else None
    except (KeyError, TypeError):
        return None


def main():
    import json
    import cv2
    from dotenv import load_dotenv
    from services.driver_monitoring.config import MonitoringConfig
    from services.driver_monitoring.face_detection_service import FaceDetectionService
    from services.driver_monitoring.face_landmark_service import FaceLandmarkService

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--camera", type=int, required=True, help="Explicit cabin webcam index")
    parser.add_argument("--output", type=Path, default=Path("data/calibration"))
    args = parser.parse_args()
    load_dotenv()
    config = MonitoringConfig.from_env()
    detector, landmarker = FaceDetectionService(config), FaceLandmarkService(config)
    print("Park safely. Close the app preview/monitoring first to release the cabin camera.")
    print("No photos or video are saved. Suggestions do not enable production alerts.")
    input("Press Enter when stationary and ready, or Ctrl+C to cancel: ")
    capture = cv2.VideoCapture(args.camera)
    rows = []
    try:
        if not capture.isOpened():
            raise RuntimeError("Cabin camera could not open")
        for label, instruction in (
            ("eyes_open", "Look ahead with eyes naturally open"),
            ("eyes_closed", "Close both eyes while stationary"),
            ("mouth_rest", "Relax your mouth, eyes open"),
            ("yawn", "Open your mouth as in a yawn"),
            ("neutral_pitch", "Look straight ahead with your head neutral"),
        ):
            input(instruction + ". Press Enter; sampling starts after 3 seconds: ")
            deadline, start, count = time.monotonic() + 23, time.monotonic() + 3, 0
            while time.monotonic() < deadline and count < 50:
                ok, frame = capture.read()
                if not ok:
                    raise RuntimeError("Cabin camera stopped delivering frames")
                now = time.monotonic()
                if now >= start:
                    face = detector.detect(frame)
                    if face["detected"]:
                        value = sample_value(label, landmarker.extract(frame, face, now))
                        if value is not None:
                            rows.append({"label": label, "value": value})
                            count += 1
                time.sleep(.1)
            if count < 20:
                raise RuntimeError("Insufficient valid samples for " + label + "; check lighting and driver seat position")
            print(label + ": " + str(count) + " samples")
        result = calibrate(rows)
        args.output.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        with (args.output / (stamp + "-samples.csv")).open("x", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=["label", "value"])
            writer.writeheader()
            writer.writerows(rows)
        target = args.output / (stamp + "-suggestions.json")
        with target.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, indent=2)
        print("Suggestions saved to " + str(target) + ". Validate separately before applying.")
    finally:
        capture.release()
        landmarker.close()


if __name__ == "__main__":
    main()
