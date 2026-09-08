"""Suggest thresholds from labelled scalar CSV data, never write deployment settings."""
import argparse
import csv
import json
import math
import statistics
from pathlib import Path


def calibrate(rows):
    groups = {}
    for row in rows:
        label = row["label"]
        value = float(row["value"])
        if not math.isfinite(value):
            raise ValueError("Calibration values must be finite")
        groups.setdefault(label, []).append(value)
    def boundary(low, high):
        a, b = sorted(groups.get(low, [])), sorted(groups.get(high, []))
        if min(len(a), len(b)) < 20:
            raise ValueError("At least 20 samples per label are required")
        upper, lower = a[int(.95*(len(a)-1))], b[int(.05*(len(b)-1))]
        if upper >= lower:
            raise ValueError("Calibration groups overlap; collect better validation samples")
        return round((upper + lower)/2, 5)
    pitch = groups.get("neutral_pitch", [])
    if len(pitch) < 20:
        raise ValueError("At least 20 neutral-pitch samples are required")
    return {"suggested_settings": {
        "DRIVER_FATIGUE_EYE_CLOSED_RATIO": boundary("eyes_closed", "eyes_open"),
        "DRIVER_FATIGUE_MOUTH_OPEN_RATIO": boundary("mouth_rest", "yawn"),
        "DRIVER_FATIGUE_NEUTRAL_PITCH": round(statistics.median(pitch), 3)},
        "deployment_validated": False,
        "note": "Validate on separate labelled trips, drivers and lighting conditions before applying. Verify positive pitch means downward movement on this camera."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path, help="Columns: label,value; eyes use max(left EAR, right EAR)")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with args.csv.open(newline="", encoding="utf-8") as stream:
        result = calibrate(csv.DictReader(stream))
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print("Calibration suggestions saved; deployment settings unchanged.")
