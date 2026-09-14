import tempfile
import unittest
from unittest.mock import Mock, patch

from services.driver_monitoring.fatigue_detection_service import FatigueConfig, FatigueDetectionService
from services.driver_monitoring.fatigue_event_store import FatigueEventStore
from scripts.calibrate_driver_fatigue import calibrate


def sample(now, eye=.3, mouth=.1, pitch=0):
    return dict(face_visible=True, quality={"available": True}, sampled_at_monotonic=now,
                eyes={"left_opening_ratio": eye, "right_opening_ratio": eye},
                mouth={"opening_ratio": mouth}, head_pose={"pitch": pitch})


class FatigueTests(unittest.TestCase):
    def setUp(self):
        self.service = FatigueDetectionService(FatigueConfig(eye_closed_ratio=.2, mouth_open_ratio=.5, neutral_pitch=0))

    def run_samples(self, start, end, **kwargs):
        events = []
        for tick in range(round(start*10), round(end*10)+1):
            now = tick/10
            state, event = self.service.update(sample(now, **kwargs), now, "driver-a")
            if event:
                events.append(event)
        return state, events

    def test_uncalibrated_and_inactive(self):
        service = FatigueDetectionService(FatigueConfig())
        self.assertEqual(service.update(sample(0), 0)[0]["status"], "uncalibrated")
        self.assertEqual(self.service.update(sample(0), 0, active=False)[0]["status"], "inactive")

    def test_short_blink_is_not_warning(self):
        self.run_samples(0, .2, eye=.1)
        state, event = self.service.update(sample(.3), .3, "driver-a")
        self.assertEqual(state["blink_count"], 1)
        self.assertAlmostEqual(state["last_blink_seconds"], .3)
        self.assertIsNone(event)

    def test_prolonged_closure_and_escalation(self):
        state, events = self.run_samples(0, 4, eye=.1)
        self.assertEqual([e["severity"] for e in events], ["medium", "high"])
        self.assertEqual(state["severity"], "high")

    def test_one_closed_eye_is_not_bilateral_closure(self):
        for tick in range(40):
            now = tick/10
            value = sample(now, eye=.1)
            value["eyes"]["right_opening_ratio"] = .3
            state, event = self.service.update(value, now)
            self.assertIsNone(event)
        self.assertEqual(state["closure_seconds"], 0)

    def test_gaps_and_invalid_data_do_not_bridge_closure(self):
        self.run_samples(0, 1, eye=.1)
        state, event = self.service.update(sample(3, eye=.1), 3, "driver-a")
        self.assertEqual(state["closure_seconds"], 0)
        self.assertIsNone(event)
        state, _ = self.service.update(sample(3.1, eye=float("nan")), 3.1, "driver-a")
        self.assertEqual(state["status"], "unavailable")

    def test_duplicate_and_stale_samples(self):
        self.service.update(sample(0, eye=.1), 0)
        for _ in range(100):
            self.service.update(sample(0, eye=.1), .1)
        self.assertEqual(len(self.service.intervals), 0)
        state, event = self.service.update(sample(0, eye=.1), 3)
        self.assertEqual(state["status"], "unavailable")
        self.assertIsNone(event)

    def test_driver_change_resets_windows(self):
        self.run_samples(0, 1.4, eye=.1)
        state, event = self.service.update(sample(1.5, eye=.1), 1.5, "driver-b")
        self.assertEqual(state["closure_seconds"], 0)
        self.assertIsNone(event)

    def test_time_weighted_perclos_and_minimum_coverage(self):
        self.run_samples(0, 9.9, eye=.1)
        state, _ = self.run_samples(10, 20)
        self.assertAlmostEqual(state["perclos"], .495)
        self.assertIn("high_eye_closure_fraction", state["reasons"])

    def test_repeated_yawns_count_once_per_episode(self):
        self.run_samples(0, 3, mouth=.8)
        self.run_samples(3.1, 3.2)
        state, events = self.run_samples(3.3, 7, mouth=.8)
        self.assertEqual(state["yawn_count"], 2)
        self.assertEqual(len(events), 1)

    def test_nods_require_neutral_calibration(self):
        self.run_samples(0, .8, pitch=25)
        self.run_samples(.9, 1)
        state, _ = self.run_samples(1.1, 2, pitch=25)
        self.assertEqual(state["nod_count"], 2)
        service = FatigueDetectionService(FatigueConfig(eye_closed_ratio=.2))
        state, _ = service.update(sample(0, pitch=80), 0)
        self.assertEqual(state["nod_count"], 0)

    def test_recovery_does_not_bypass_cooldown(self):
        self.run_samples(0, 2, eye=.1)
        self.run_samples(2.1, 2.2)
        _, events = self.run_samples(2.3, 4.3, eye=.1)
        self.assertEqual(events, [])

    def test_long_run_memory_is_bounded(self):
        state, events = self.run_samples(0, 600)
        self.assertLessEqual(len(self.service.intervals), 601)
        self.assertEqual(events, [])

    def test_configuration_validation(self):
        for values in ({"max_gap": 2}, {"eye_closed_ratio": float("nan")}, {"critical_seconds": .1}):
            with self.assertRaises(ValueError):
                FatigueConfig(**values)

    def test_extreme_head_rotation_suppresses_unreliable_eyes(self):
        value = sample(0, eye=.1)
        value["head_pose"]["yaw"] = 60
        state, event = self.service.update(value, 0)
        self.assertEqual(state["status"], "unavailable")
        self.assertIsNone(event)


class EventTests(unittest.TestCase):
    def test_recording_stop_during_event_uses_captured_time_origin(self):
        from services.driver_monitoring.config import MonitoringConfig
        from services.driver_monitoring.driver_monitoring_service import DriverMonitoringService
        now = [0]
        service = DriverMonitoringService(MonitoringConfig(), store=Mock(revision=0), clock=lambda: now[0])
        service.fatigue = FatigueDetectionService(FatigueConfig(eye_closed_ratio=.2))
        service.recording_context("video-a")
        update = service.fatigue.update
        def stop_on_event(*args):
            state, event = update(*args)
            if event:
                service.recording_context()
            return state, event
        with patch.object(service.fatigue, "update", side_effect=stop_on_event):
            for tick in range(16):
                now[0] = tick/10
                service.measurements = sample(now[0], eye=.1)
                service._update_fatigue(now[0])
        self.assertEqual(service.fatigue_events[0]["video_id"], "video-a")
        self.assertEqual(service.fatigue_events[0]["video_offset_seconds"], 1.5)
        self.assertIsNone(service.recording_started)

    def test_pre_recording_frame_cannot_seed_new_recording(self):
        from services.driver_monitoring.config import MonitoringConfig
        from services.driver_monitoring.driver_monitoring_service import DriverMonitoringService
        service = DriverMonitoringService(MonitoringConfig(), store=Mock(revision=0), clock=lambda: 10)
        service.fatigue = FatigueDetectionService(FatigueConfig(eye_closed_ratio=.2))
        service.recording_context("video-a")
        service.measurements = sample(9.9, eye=.1)
        service._update_fatigue(9.9)
        self.assertIsNone(service.fatigue.last)
        self.assertFalse(service.fatigue_events)

    def test_frame_to_disk_to_existing_incident_list(self):
        import numpy as np
        from services.driver_monitoring.config import MonitoringConfig
        from services.driver_monitoring.runtime import DriverMonitoringRuntime
        from services.incident_service import IncidentService
        runtime = DriverMonitoringRuntime(MonitoringConfig(recognition_enabled=False))
        service = runtime.service
        now = [0]
        service.clock = lambda: now[0]
        service.fatigue = FatigueDetectionService(FatigueConfig(eye_closed_ratio=.2))
        service.detector = Mock(error=None)
        service.detector.detect.return_value = dict(detected=True, bbox=[0, 0, 40, 40])
        service.landmarker = Mock(error=None)
        service.landmarker.extract.side_effect = lambda frame, face, stamp: sample(stamp, eye=.1)
        service.recording_context("video-evidence")
        with tempfile.TemporaryDirectory(dir=".") as directory:
            store = FatigueEventStore(directory)
            with patch("services.driver_monitoring.fatigue_event_store.FatigueEventStore", return_value=store), patch("services.gps_service.GPSService.latest", return_value=None):
                for tick in range(21):
                    now[0] = tick/5
                    runtime._publish(service.process_frame(np.zeros((48, 48, 3), dtype=np.uint8), now[0]))
                incidents = IncidentService.__new__(IncidentService)
                incidents.store = Mock()
                incidents.store.read.return_value = []
                rows = incidents.list_incidents()
                self.assertEqual(len(rows), 2)
                self.assertTrue(all(row["linked_video_id"] == "video-evidence" for row in rows))
                self.assertEqual({row["severity"] for row in rows}, {"medium", "high"})
                self.assertTrue(all(row["type"] == "driver fatigue" for row in rows))
                self.assertFalse(service.fatigue_events)

    def test_replay_uses_production_detector_and_rejects_reversed_time(self):
        from scripts.evaluate_driver_fatigue import evaluate
        rows = [dict(timestamp_seconds=str(t/10), left_eye=".1", right_eye=".1", expected_warning="1" if t >= 15 else "0") for t in range(21)]
        report = evaluate(rows, FatigueConfig(eye_closed_ratio=.2))
        self.assertEqual(report["sample_counts"]["false_positive"], 0)
        self.assertEqual(report["sample_counts"]["false_negative"], 0)
        self.assertEqual(report["meaningful_events"], 1)
        with self.assertRaises(ValueError):
            evaluate(list(reversed(rows)), FatigueConfig(eye_closed_ratio=.2))

    def test_storage_failure_has_backoff_and_keeps_bounded_pending_event(self):
        from services.driver_monitoring.runtime import DriverMonitoringRuntime
        from services.driver_monitoring.config import MonitoringConfig
        runtime = DriverMonitoringRuntime(MonitoringConfig(enabled=False))
        runtime.service.fatigue_events.append({"event_id": "test"})
        with patch("services.driver_monitoring.fatigue_event_store.FatigueEventStore.save", side_effect=OSError) as save, patch("services.gps_service.GPSService.latest", return_value=None):
            for _ in range(10):
                runtime._publish(runtime.service.result())
        self.assertEqual(save.call_count, 1)
        self.assertEqual(len(runtime.service.fatigue_events), 1)
        self.assertTrue(runtime._fatigue_write_failed)

    def test_aggregate_persistence_and_gps_freshness(self):
        event = dict(event_id="fatigue_" + "a"*32, video_id="video-a", timestamp="2026-09-07T10:00:10+00:00",
                     severity="high", score=90, reasons=["prolonged_eye_closure"], frames="must not persist")
        gps = dict(video_id="video-a", timestamp="2026-09-07T10:00:00+00:00", latitude=1, longitude=2)
        with tempfile.TemporaryDirectory(dir=".") as directory:
            store = FatigueEventStore(directory)
            saved = store.save(event, gps)
            self.assertIsNotNone(saved["gps"])
            self.assertNotIn("frames", saved)
            store.save(event, gps)
            self.assertEqual(len(store.list_events()), 1)
            self.assertEqual(store.incidents()[0]["linked_video_id"], "video-a")
            gps["video_id"] = "old-video"
            self.assertIsNone(store.save(event, gps)["gps"])
            self.assertEqual(store.list_events("other-video"), [])

    def test_calibration_rejects_overlap_and_requires_samples(self):
        rows = [{"label": label, "value": value} for label, value in
                (("eyes_closed", .1), ("eyes_open", .3), ("mouth_rest", .1), ("yawn", .7), ("neutral_pitch", -5)) for _ in range(20)]
        settings = calibrate(rows)["suggested_settings"]
        self.assertEqual(settings["DRIVER_FATIGUE_EYE_CLOSED_RATIO"], .2)
        with self.assertRaises(ValueError):
            calibrate(rows[:10])
        for row in rows:
            if row["label"] == "eyes_closed":
                row["value"] = .4
        with self.assertRaises(ValueError):
            calibrate(rows)

    def test_shared_worker_creates_linked_event(self):
        from services.driver_monitoring.config import MonitoringConfig
        from services.driver_monitoring.driver_monitoring_service import DriverMonitoringService
        service = DriverMonitoringService(MonitoringConfig(), store=Mock(revision=0), clock=lambda: 0)
        service.fatigue = FatigueDetectionService(FatigueConfig(eye_closed_ratio=.2))
        service.active = True
        service.recording_started = 0
        service.sessions.context(video_id="video-a", trip_id="trip-a")
        for tick in range(21):
            now = tick/10
            service.clock = lambda: now
            service.measurements = sample(now, eye=.1)
            service._update_fatigue(now)
        self.assertEqual(len(service.fatigue_events), 1)
        event = service.fatigue_events[0]
        self.assertEqual(event["video_id"], "video-a")
        self.assertEqual(event["trip_id"], "trip-a")
        self.assertEqual(event["driver_identity_status"], "unknown")
        self.assertAlmostEqual(event["video_offset_seconds"], 1.5)
        # A prolonged camera gap clears history even when identity is unknown.
        service._fatigue_last_frame = 2
        service.clock = lambda: 10
        service.process_frame(None, 10)
        self.assertEqual(len(service.fatigue.intervals), 0)
        self.assertEqual(service.fatigue.state["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
