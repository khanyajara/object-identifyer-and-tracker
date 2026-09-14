"""Observe the existing app process and cameras; never opens cameras or writes video."""
import math
import statistics
import threading
import time


def summarize(samples):
    def distribution(values):
        values = sorted(v for v in values if isinstance(v, (int, float)) and math.isfinite(v))
        if not values:
            return None
        return {"min": min(values), "median": statistics.median(values), "p95": values[max(0, math.ceil(.95 * len(values))-1)], "max": max(values)}
    roles = sorted({role for sample in samples for role in sample["cameras"]})
    cameras = {}
    for role in roles:
        rows = [sample["cameras"][role] for sample in samples if role in sample["cameras"]]
        cameras[role] = {"fps": distribution([r["live_fps"] for r in rows]),
                         "recording_samples": sum(r["recording"] for r in rows),
                         "frames_written_delta": max(0, rows[-1]["frames_written"]-rows[0]["frames_written"]),
                         "read_failures_delta": max(0, rows[-1]["read_failures"]-rows[0]["read_failures"])}
    timing_keys = sorted({key for s in samples for key in s["inference_ms"]})
    return {"samples": len(samples), "observed_seconds": samples[-1]["elapsed"] - samples[0]["elapsed"] if len(samples) > 1 else 0,
            "cpu_one_core_percent": distribution([s["cpu"] for s in samples]),
            "ram_mib": distribution([s["ram_mib"] for s in samples]), "cameras": cameras,
            "inference_ms": {k: distribution([s["inference_ms"].get(k) for s in samples]) for k in timing_keys},
            "pending_frames_peak": max((s["pending_frames"] for s in samples), default=0),
            "driver_running_samples": sum(s["driver_running"] for s in samples),
            "both_cameras_recording_samples": sum(all(s["cameras"].get(role, {}).get("recording", False) for role in ("front", "rear")) for s in samples),
            "note": "Measured app-process resources and existing capture statistics, not browser-render FPS or a model accuracy test. CPU is percent of one logical core; it can exceed 100. Inference statistics are snapshots of the most recent stage timings, not a per-inference latency distribution."}


class PerformanceProbe:
    def __init__(self):
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._status = {"state": "idle"}

    @property
    def running(self):
        return bool(self._thread and self._thread.is_alive())

    def snapshot(self):
        with self._lock:
            return dict(self._status)

    def stop(self):
        self._stop.set()

    def start(self, manager, runtime, seconds=60):
        if self.running:
            raise ValueError("A performance capture is already running")
        if not 10 <= seconds <= 300:
            raise ValueError("Choose 10–300 seconds")
        self._stop.clear()
        with self._lock:
            self._status = {"state": "running", "elapsed": 0}
        def run():
            try:
                import psutil
                process = psutil.Process()
                process.cpu_percent()
                began, samples = time.monotonic(), []
                while not self._stop.wait(1):
                    elapsed = time.monotonic() - began
                    camera_rows = manager.status()
                    diagnostics = runtime.diagnostics()
                    samples.append({"elapsed": elapsed, "cpu": process.cpu_percent(),
                                    "ram_mib": process.memory_info().rss / 1024**2,
                                    "driver_running": runtime.running,
                                    "pending_frames": diagnostics["pending_frames"],
                                    "inference_ms": diagnostics.get("inference_timings_ms", {}),
                                    "cameras": {role: {key: row[key] for key in ("live_fps", "recording", "frames_written", "read_failures")} for role, row in camera_rows.items()}})
                    with self._lock:
                        self._status = {"state": "running", "elapsed": round(elapsed)}
                    if elapsed >= seconds:
                        break
                report = summarize(samples)
                report["completed_requested_duration"] = bool(samples and samples[-1]["elapsed"] >= seconds)
                with self._lock:
                    self._status = {"state": "complete", "report": report}
            except Exception:
                with self._lock:
                    self._status = {"state": "failed", "error": "Performance capture unavailable; check cameras and monitoring diagnostics."}
        self._thread = threading.Thread(target=run, name="driver-performance-probe", daemon=True)
        self._thread.start()


def render_performance_probe(manager):
    import json
    import streamlit as st
    from services.auth_service import AdminAuthService
    from .runtime import get_runtime
    if "driver_performance_probe" not in st.session_state:
        st.session_state.driver_performance_probe = PerformanceProbe()
    probe = st.session_state.driver_performance_probe
    with st.expander("Driver monitoring performance test"):
        st.caption("Measure this app for 60 seconds with the current cameras and recording mode. Start recording separately to measure the full workload.")
        if st.button("Measure performance for 60 seconds", disabled=probe.running):
            probe.start(manager, get_runtime())
        if st.button("Stop performance measurement", disabled=not probe.running):
            probe.stop()
        @st.fragment(run_every=1)
        def progress():
            try:
                actor = AdminAuthService().decode_access_token(st.session_state.get("admin_token", ""))
                if actor["role"] not in {"admin", "super_admin"}:
                    return
            except (ValueError, RuntimeError):
                return
            state = probe.snapshot()
            if state["state"] == "running":
                st.caption(f"Measuring: {state['elapsed']} seconds")
            elif state["state"] == "complete":
                st.json(state["report"])
                st.download_button("Download performance report", json.dumps(state["report"], indent=2), "driver-performance.json", mime="application/json")
            elif state["state"] == "failed":
                st.error(state["error"])
        progress()
