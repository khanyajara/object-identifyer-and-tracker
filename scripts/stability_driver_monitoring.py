"""Prolonged background-service test with synthetic capture/model/database adapters.

No webcam, network, face images, enrollment or cloud writes. This measures queue,
thread, cache and resource stability; it is not a native model performance test.
"""
import argparse
import json
from pathlib import Path
import sys
import time
from unittest.mock import patch
from contextlib import ExitStack

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=180)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--fatigue", action="store_true", help="Exercise calibrated synthetic fatigue and mock event persistence")
    args = parser.parse_args()
    import cv2
    import numpy as np
    import psutil
    from core.dual_camera import CameraChannel, CameraConfig
    from services.driver_monitoring.config import MonitoringConfig
    from services.driver_monitoring.runtime import DriverMonitoringRuntime
    from services.driver_monitoring.driver_monitoring_service import DriverMonitoringService
    from services.driver_monitoring.face_enrollment_service import FaceEnrollmentService
    from services.driver_monitoring.fatigue_detection_service import FatigueConfig, FatigueDetectionService
    frame = np.zeros((480, 640, 3), np.uint8)
    counts = dict(camera_opens=0, camera_releases=0, detector_initializations=0,
                  recognizer_initializations=0, landmark_initializations=0, database_reads=0, database_writes=0)
    counts["fatigue_event_writes"] = 0
    began_signals = time.monotonic()

    class Capture:
        def __init__(self):
            counts["camera_opens"] += 1
            self.opened = True

        def isOpened(self):
            return self.opened

        def get(self, key):
            return {cv2.CAP_PROP_FRAME_WIDTH: 640, cv2.CAP_PROP_FRAME_HEIGHT: 480, cv2.CAP_PROP_FPS: 30}.get(key, 0)

        def read(self):
            time.sleep(1 / 30)
            return self.opened, frame.copy() if self.opened else None

        def release(self):
            if self.opened:
                counts["camera_releases"] += 1
            self.opened = False

    class Detector:
        error = None

        def __init__(self):
            counts["detector_initializations"] += 1

        def detect(self, image):
            return dict(detected=True, bbox=[240, 150, 160, 180], ambiguous=False)

    class Recognizer:
        def __init__(self, config):
            self.config = config
            counts["recognizer_initializations"] += 1

        def recognize(self, image, face, profiles):
            time.sleep(.1)
            return dict(driver_id="SYNTHETIC", display_name="Synthetic driver", confidence=.9)

    class Landmarker:
        error = None

        def __init__(self):
            counts["landmark_initializations"] += 1

        def extract(self, image, face, now):
            time.sleep(.13)  # slower than producer to deliberately stress dropping
            eye = .1 if (now-began_signals) % 40 >= 34 else .3
            return dict(face_visible=True, eyes={"left_opening_ratio": eye, "right_opening_ratio": eye},
                        mouth={"opening_ratio": .1}, head_pose={}, quality={"available": True}, sampled_at_monotonic=now)

        def close(self):
            pass

    class Store:
        revision = 0

        def recognition_profiles(self):
            counts["database_reads"] += 1
            return [{"driver_id": "SYNTHETIC"}]

        def mark_recognized(self, driver_id):
            counts["database_writes"] += 1

    config = MonitoringConfig(threshold=.8)
    runtime = DriverMonitoringRuntime(config)
    runtime.service = DriverMonitoringService(config, Detector(), Recognizer(config), Landmarker(), Store())
    runtime.service.set_active(True)
    if args.fatigue:
        runtime.service.fatigue = FatigueDetectionService(FatigueConfig(eye_closed_ratio=.2))
        runtime.service.sessions.context(video_id="synthetic-video")
        runtime.service.recording_started = time.monotonic()
    def save_event(event, gps=None):
        counts["fatigue_event_writes"] += 1
        return event
    mocks = ExitStack()
    mocks.enter_context(patch("services.driver_monitoring.fatigue_event_store.FatigueEventStore.save", side_effect=save_event))
    mocks.enter_context(patch("services.gps_service.GPSService.latest", return_value=None))
    runtime.enrollment = FaceEnrollmentService(runtime.service.detector, runtime.service.recognizer, runtime.service.store)
    channel = CameraChannel(CameraConfig(index=99, role="driver", width=640, height=480, ai_enabled=False))
    channel.keep_capture_alive = True
    channel._open_capture = Capture
    channel._measure_fps = lambda cap: 30
    runtime.channel = channel
    process = psutil.Process()
    baseline_threads = process.num_threads()
    runtime.start(source=channel.get_latest)
    process.cpu_percent(None)
    began = time.monotonic()
    samples = []
    previous_report = 0
    try:
        while time.monotonic() - began < args.seconds:
            time.sleep(.5)
            elapsed = time.monotonic() - began
            runtime.start(source=channel.get_latest)  # simulate frequent Streamlit reruns
            samples.append({"elapsed": elapsed, "rss_mib": process.memory_info().rss / 1024**2,
                            "threads": process.num_threads(), "cpu_one_core_percent": process.cpu_percent(None),
                            "queue_depth": runtime._frames.qsize()})
            if elapsed - previous_report >= 30:
                previous_report = elapsed
                print(f"Stability probe: {int(elapsed)} seconds; queue={runtime._frames.qsize()}; threads={process.num_threads()}", flush=True)
        metrics = runtime.diagnostics()
        final_identity = runtime.snapshot()["identity_status"]
    finally:
        runtime.stop()
        mocks.close()
    warm = [sample for sample in samples if sample["elapsed"] >= min(10, args.seconds/4)] or samples
    result = {"duration_seconds": round(time.monotonic()-began, 2),
              "mode": "synthetic capture, models and database; no native inference or network",
              "baseline_threads": baseline_threads, "peak_threads": max(s["threads"] for s in samples),
              "threads_after_shutdown": process.num_threads(), "queue_peak": max(s["queue_depth"] for s in samples),
              "warm_rss_min_mib": round(min(s["rss_mib"] for s in warm), 2),
              "warm_rss_max_mib": round(max(s["rss_mib"] for s in warm), 2),
              "warm_rss_growth_mib": round(warm[-1]["rss_mib"]-warm[0]["rss_mib"], 2),
              "mean_cpu_one_core_percent": round(sum(s["cpu_one_core_percent"] for s in warm)/len(warm), 2),
              "final_identity_status": final_identity, "rerun_simulations": len(samples),
              "fatigue_enabled": args.fatigue,
              "counters": counts, "runtime": metrics}
    result["passed"] = (result["queue_peak"] <= 1 and counts["camera_opens"] == 1
                        and counts["camera_releases"] == 1 and result["threads_after_shutdown"] <= baseline_threads
                        and result["warm_rss_growth_mib"] < 10 and final_identity == "verified"
                        and counts["database_reads"] <= 1 + int(args.seconds / config.index_ttl)
                        and counts["database_writes"] == 1)
    if args.fatigue and args.seconds >= 45:
        result["passed"] = result["passed"] and counts["fatigue_event_writes"] >= 1
    if args.output:
        args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
