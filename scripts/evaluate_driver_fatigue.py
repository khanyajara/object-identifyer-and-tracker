"""Replay labelled scalar measurements through the production temporal detector."""
import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.driver_monitoring.fatigue_detection_service import FatigueConfig, FatigueDetectionService


def evaluate(rows, config):
    service = FatigueDetectionService(config)
    counts = dict(true_positive=0, false_positive=0, true_negative=0, false_negative=0, unavailable=0)
    events = 0
    previous = None
    for row in rows:
        now = float(row["timestamp_seconds"])
        if previous is not None and now <= previous:
            raise ValueError("Replay timestamps must strictly increase")
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
        if state["status"] != "monitoring":
            counts["unavailable"] += 1
            continue
        predicted, expected = state["severity"] != "none", row["expected_warning"] == "1"
        counts[("true_" if predicted == expected else "false_") + ("positive" if predicted else "negative")] += 1
    return {"sample_counts": counts, "meaningful_events": events,
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
