"""Replay labelled scalar measurements through the production temporal detector."""
import argparse
import csv
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.driver_monitoring.fatigue_detection_service import FatigueConfig, FatigueDetectionService


def evaluate(rows, config):
    service = FatigueDetectionService(config)
    counts = dict(true_positive=0, false_positive=0, true_negative=0, false_negative=0, unavailable=0)
    events = 0
    false_events = 0
    observed_seconds = false_warning_seconds = 0.0
    last_state = None
    previous = None
    for row in rows:
        now = float(row["timestamp_seconds"])
        if not math.isfinite(now):
            raise ValueError("Replay timestamps must be finite")
        if previous is not None and now <= previous:
            raise ValueError("Replay timestamps must strictly increase")
        gap = now - previous if previous is not None else 0
        previous = now
        if row["expected_warning"] not in {"0", "1"}:
            raise ValueError("expected_warning must be 0 or 1")
        value = dict(face_visible=row.get("face_visible", "1") == "1", quality={"available": True},
                     sampled_at_monotonic=now,
                     eyes={"left_opening_ratio": float(row["left_eye"]), "right_opening_ratio": float(row["right_eye"])},
                     mouth={}, head_pose={})
        if row.get("mouth"):
            value["mouth"]["opening_ratio"] = float(row["mouth"])
        for axis in ("pitch", "yaw", "roll"):
            if row.get(axis):
                value["head_pose"][axis] = float(row[axis])
        state, event = service.update(value, now, (row.get("driver_id"), row.get("video_id")))
        events += event is not None
        false_events += event is not None and row["expected_warning"] == "0"
        expected = row["expected_warning"] == "1"
        context = (row.get("driver_id"), row.get("video_id"))
        if state["status"] == "monitoring" and last_state and last_state["available"] and context == last_state["context"] and 0 < gap <= config.max_gap:
            observed_seconds += gap
            if last_state["predicted"] and not last_state["expected"]:
                false_warning_seconds += gap
        last_state = {"available": state["status"] == "monitoring", "predicted": state["severity"] != "none", "expected": expected, "context": context}
        if state["status"] != "monitoring":
            counts["unavailable"] += 1
            continue
        predicted, expected = state["severity"] != "none", row["expected_warning"] == "1"
        counts[("true_" if predicted == expected else "false_") + ("positive" if predicted else "negative")] += 1
    tp, fp, fn = counts["true_positive"], counts["false_positive"], counts["false_negative"]
    return {"sample_counts": counts, "meaningful_events": events,
            "false_warning_events": false_events,
            "observed_seconds": observed_seconds, "false_warning_seconds": false_warning_seconds,
            "precision": tp/(tp+fp) if tp+fp else None,
            "recall_on_available_samples": tp/(tp+fn) if tp+fn else None,
            "false_warning_events_per_observed_hour": false_events*3600/observed_seconds if observed_seconds else None,
            "note": "Sample-based replay metrics; not a real-camera or driving safety validation."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    with args.csv.open(newline="", encoding="utf-8") as source:
        report = evaluate(csv.DictReader(source), FatigueConfig.from_env())
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
