"""Offline resource probe: no camera, cloud access, enrollment or stored images.

Run from project root: python scripts/benchmark_driver_monitoring.py
An optional --image path must be a consented cabin test frame. Only aggregate
timings and RSS are printed. Native model measurements require local weights.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", type=Path)
    parser.add_argument("--samples", type=int, default=20)
    args = parser.parse_args()
    import psutil
    process = psutil.Process(os.getpid())
    rss = lambda: round(process.memory_info().rss / 1024**2, 2)
    report = {"python": sys.version.split()[0], "logical_cpus": psutil.cpu_count(), "python_rss_mib": rss()}
    began = time.perf_counter()
    import streamlit_app  # imports only; does not execute main or open cameras
    report.update(roadwatch_import_seconds=round(time.perf_counter()-began,3), roadwatch_import_rss_mib=rss())
    cpu_start = process.cpu_times()
    time.sleep(.5)
    cpu_end = process.cpu_times()
    report["idle_cpu_one_core_percent"] = round(((cpu_end.user-cpu_start.user)+(cpu_end.system-cpu_start.system))/.5*100,2)
    import cv2
    import numpy as np
    from services.driver_monitoring.config import MonitoringConfig
    from services.driver_monitoring.driver_monitoring_service import DriverMonitoringService
    from services.driver_monitoring.face_detection_service import FaceDetectionService
    from services.driver_monitoring.face_recognition_service import FaceRecognitionService
    from services.driver_monitoring.face_landmark_service import FaceLandmarkService
    config = MonitoringConfig()
    frame = cv2.imread(str(args.image)) if args.image else np.zeros((480,640,3),np.uint8)
    if frame is None:
        parser.error("Image could not be decoded.")
    from dataclasses import replace
    disabled = DriverMonitoringService(replace(config, enabled=False))
    began=time.perf_counter()
    for _ in range(1000):
        disabled.process_frame(frame)
    report["disabled_call_mean_ms"] = round((time.perf_counter()-began),4)
    report["disabled_model_load_attempted"] = disabled.detector._attempted
    report["disabled_rss_mib"] = rss()
    detector, recognizer, landmarker = FaceDetectionService(config), FaceRecognitionService(config), FaceLandmarkService(config)
    report["stages"] = {}
    face = None
    for stage, path in (("detection",config.yunet_path),("recognition",config.sface_path),("landmarks",config.landmarker_path)):
        if not path.is_file():
            report["stages"][stage] = {"available":False,"reason":"model file missing"}
            continue
        if stage != "detection" and (not face or not face["detected"]):
            report["stages"][stage] = {"available":False,"reason":"consented frame with detectable face required"}
            continue
        durations=[]
        cpu_start=process.cpu_times()
        start=time.perf_counter()
        try:
            for _ in range(max(1,args.samples)):
                began=time.perf_counter()
                if stage == "detection":
                    face=detector.detect(frame)
                    if detector.error:
                        raise RuntimeError()
                elif stage == "recognition":
                    recognizer.embedding(frame,face)
                else:
                    landmarker.extract(frame,face,time.monotonic())
                    if landmarker.error:
                        raise RuntimeError()
                durations.append((time.perf_counter()-began)*1000)
            cpu_end=process.cpu_times()
            report["stages"][stage] = dict(available=True,first_call_ms=round(durations[0],2),
                median_ms=round(float(np.median(durations)),2), p95_ms=round(float(np.percentile(durations,95)),2),rss_mib=rss(),
                cpu_one_core_percent=round(((cpu_end.user-cpu_start.user)+(cpu_end.system-cpu_start.system))/(time.perf_counter()-start)*100,2))
        except Exception:
            report["stages"][stage] = {"available":False,"reason":"model/runtime unavailable"}
    landmarker.close()
    report["note"] = "Import baseline, not a live recorder/YOLO benchmark. CPU percent is per logical core; native stages are burst inference, not scheduled duty cycle."
    print(json.dumps(report,indent=2))


if __name__ == "__main__":
    main()
