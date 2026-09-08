"""Read-only native preflight. Never opens a camera or queries private records."""
import argparse
import hashlib
import importlib.metadata
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.driver_monitoring.config import MonitoringConfig
from services.driver_monitoring.fatigue_detection_service import FatigueConfig


def validate():
    import cv2
    import numpy as np
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    report = {"python": sys.version.split()[0], "opencv": cv2.__version__, "checks": {},
              "real_camera_validated": False, "recognition_accuracy_validated": False,
              "fatigue_accuracy_validated": False}
    checks = report["checks"]
    try:
        config, fatigue = MonitoringConfig.from_env(), FatigueConfig.from_env()
    except ValueError:
        checks["configuration"] = False
        report["ready_for_camera_acceptance"] = False
        return report
    for name, path in (("yunet", config.yunet_path), ("sface", config.sface_path), ("landmarker", config.landmarker_path)):
        checks[name + "_asset"] = path.is_file() and path.stat().st_size > 10000
        if checks[name + "_asset"]:
            report[name + "_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    try:
        detector = cv2.FaceDetectorYN.create(str(config.yunet_path), "", (config.width, config.height))
        checks["yunet_native_load"] = True
        frame = np.zeros((config.height, config.width, 3), dtype=np.uint8)
        detector.detect(frame)
        began = time.perf_counter()
        for _ in range(20):
            detector.detect(frame)
        report["yunet_blank_frame_mean_ms"] = round((time.perf_counter()-began)*1000/20, 3)
        checks["yunet_native_forward"] = True
    except Exception:
        checks["yunet_native_load"] = False
        checks["yunet_native_forward"] = False
    try:
        cv2.FaceRecognizerSF.create(str(config.sface_path), "")
        checks["sface_native_load"] = True
    except Exception:
        checks["sface_native_load"] = False
    try:
        import mediapipe as mp
        report["mediapipe"] = importlib.metadata.version("mediapipe")
        options = mp.tasks.vision.FaceLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(config.landmarker_path)),
            running_mode=mp.tasks.vision.RunningMode.VIDEO, num_faces=1,
            output_facial_transformation_matrixes=True)
        with mp.tasks.vision.FaceLandmarker.create_from_options(options):
            checks["landmarker_native_load"] = True
    except Exception:
        checks["landmarker_native_load"] = False
    checks["recognition_threshold_configured"] = config.threshold is not None
    checks["eye_threshold_configured"] = fatigue.eye_closed_ratio is not None
    checks["mouth_threshold_configured"] = fatigue.mouth_open_ratio is not None
    checks["neutral_pitch_configured"] = fatigue.neutral_pitch is not None
    checks["camera_source_configured"] = bool(os.getenv("DRIVER_CAMERA_INDEX") or os.getenv("DRIVER_CAMERA_SOURCE") == "rear")
    report["ready_for_camera_acceptance"] = all(checks.values())
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = validate()
    rendered = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    sys.exit(0 if report["ready_for_camera_acceptance"] else 2)
