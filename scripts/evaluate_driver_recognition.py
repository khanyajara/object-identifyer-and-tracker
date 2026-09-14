"""Calibrate SFace on labelled images, then evaluate untouched test sessions."""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def validate_manifest(rows):
    sessions, files = {}, set()
    counts = {key: 0 for key in ("enroll", "calibration", "test")}
    for row in rows:
        split = row["split"]
        if split not in counts or not row["person_id"].strip() or not row["session_id"].strip():
            raise ValueError("Every image needs person_id, session_id and split=enroll/calibration/test")
        session = row["session_id"]
        if session in sessions and sessions[session] != split:
            raise ValueError("A capture session cannot cross dataset splits")
        sessions[session] = split
        if row["path"] in files:
            raise ValueError("Images must not be reused")
        files.add(row["path"])
        counts[split] += 1
    if not all(counts.values()):
        raise ValueError("Enrollment, calibration and test samples are all required")


def metrics(rows, threshold):
    counts = dict(known=0, unknown=0, unavailable=0, correct=0, rejected_known=0, wrong_identity=0, accepted_unknown=0)
    for row in rows:
        known = row["known"]
        counts["known" if known else "unknown"] += 1
        if row.get("score") is None:
            counts["unavailable"] += 1
            if known:
                counts["rejected_known"] += 1
            continue
        if not all(math.isfinite(row[key]) for key in ("score", "runner_up")):
            raise ValueError("Recognition scores must be finite")
        accepted = row["score"] >= threshold and row["score"] - row["runner_up"] >= .05
        if not known:
            counts["accepted_unknown"] += int(accepted)
        elif not accepted:
            counts["rejected_known"] += 1
        elif row["correct_candidate"]:
            counts["correct"] += 1
        else:
            counts["wrong_identity"] += 1
    return {**counts,
            "unknown_false_accept_rate": counts["accepted_unknown"] / counts["unknown"] if counts["unknown"] else None,
            "known_correct_rate": counts["correct"] / counts["known"] if counts["known"] else None,
            "coverage": (len(rows) - counts["unavailable"]) / len(rows) if rows else None}


def choose_threshold(rows):
    known = [r for r in rows if r["known"] and r.get("score") is not None]
    unknown = [r for r in rows if not r["known"] and r.get("score") is not None]
    if min(len(known), len(unknown)) < 20:
        raise ValueError("Need at least 20 usable known and 20 usable unknown calibration probes")
    values = sorted(set(r["score"] for r in rows if r.get("score") is not None))
    candidates = {1.0} | {v for v in values if 0 < v <= 1} | {(a+b)/2 for a, b in zip(values, values[1:]) if 0 < (a+b)/2 <= 1}
    valid = []
    for threshold in candidates:
        result = metrics(rows, threshold)
        if result["accepted_unknown"] == result["wrong_identity"] == 0 and result["correct"]:
            valid.append((result["correct"], threshold))
    if not valid:
        raise ValueError("No useful threshold separates these calibration identities. Improve samples before deployment.")
    return max(valid)[1]


def evaluate_manifest(rows, root, config):
    import cv2
    import numpy as np
    from services.driver_monitoring.face_detection_service import FaceDetectionService
    from services.driver_monitoring.face_recognition_service import FaceRecognitionService, normalize_embedding
    validate_manifest(rows)
    detector, recognizer = FaceDetectionService(config), FaceRecognitionService(config)
    samples, hashes = [], set()
    for row in rows:
        path = (root / row["path"]).resolve()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest in hashes:
            raise ValueError("Duplicate image contents detected; use independent samples")
        hashes.add(digest)
        frame = cv2.imread(str(path))
        if frame is None:
            raise ValueError("A manifest image could not be decoded")
        face = detector.detect(frame)
        embedding = None
        if face["detected"] and len(face["faces"]) == 1:
            x, y, w, h = map(int, face["bbox"])
            fh, fw = frame.shape[:2]
            if min(w, h) >= 80 and x >= 0 and y >= 0 and x+w <= fw and y+h <= fh:
                crop = cv2.cvtColor(frame[y:y+h, x:x+w], cv2.COLOR_BGR2GRAY)
                if 40 < float(crop.mean()) < 220 and cv2.Laplacian(crop, cv2.CV_64F).var() >= 60:
                    embedding = recognizer.embedding(frame, face)
        samples.append((row, embedding))
    gallery = {}
    for row, embedding in samples:
        if row["split"] == "enroll":
            gallery.setdefault(row["person_id"], [])
            if embedding is not None:
                gallery[row["person_id"]].append(embedding)
    if any(len(values) < 5 for values in gallery.values()):
        raise ValueError("Each enrolled identity needs five usable images with varied natural poses")
    gallery = {person: normalize_embedding(np.mean(values, axis=0)) for person, values in gallery.items()}
    probes = {"calibration": [], "test": []}
    for row, embedding in samples:
        if row["split"] == "enroll":
            continue
        probe = {"known": row["person_id"] in gallery, "score": None}
        if embedding is not None:
            ranked = sorted(((float(np.clip(embedding @ value, -1, 1)), person) for person, value in gallery.items()), reverse=True)
            probe.update(score=ranked[0][0], runner_up=ranked[1][0] if len(ranked) > 1 else -1.0, correct_candidate=ranked[0][1] == row["person_id"])
        probes[row["split"]].append(probe)
    threshold = choose_threshold(probes["calibration"])
    test = metrics(probes["test"], threshold)
    if min(test["known"], test["unknown"]) < 20:
        raise ValueError("Test split also needs at least 20 known and 20 unknown probes")
    return {"suggested_settings": {"FACE_RECOGNITION_THRESHOLD": threshold},
            "calibration": metrics(probes["calibration"], threshold), "test": test,
            "enrolled_identity_count": len(gallery), "deployment_validated": False,
            "note": "Frozen SFace embeddings; no model weight training. Frame-level gallery matching, not temporal identity-session validation. Zero observed false accepts is not proof of a zero population rate. No images or embeddings saved by this evaluator."}


def main():
    from dotenv import load_dotenv
    from services.driver_monitoring.config import MonitoringConfig
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="CSV columns: path,person_id,session_id,split")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    with args.manifest.open(newline="", encoding="utf-8") as stream:
        report = evaluate_manifest(list(csv.DictReader(stream)), args.manifest.parent, MonitoringConfig.from_env())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("Recognition report saved. Deployment thresholds remain unchanged.")


if __name__ == "__main__":
    main()
