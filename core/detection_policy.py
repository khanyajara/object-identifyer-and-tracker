"""Shared object confidence and overlapping-box policy."""

import math
from collections import Counter

MIN_CONFIDENCE = 0.88
DUPLICATE_IOU = 0.70


def empty_recording_summary():
    return {"people_count_max": 0, "vehicle_count_max": 0, "plates_detected": [],
            "movement_events": 0, "object_counts": {}, "unique_tracking_ids": [], "snapshots": 0}


def update_recording_summary(summary, event, tracked_labels, previous_movement):
    """Shared single/dual-camera counting; retain a track's first observed label."""
    for kind in ("people", "vehicle"):
        key = kind + "_count_max"
        summary[key] = max(summary[key], event.get(kind + "_count", 0))
    movement = bool(event.get("movement_detected"))
    summary["movement_events"] += int(movement and not previous_movement)
    labels = []
    for item in event.get("objects", []):
        label = item.get("label", "unknown")
        if item.get("tracking_id") is not None:
            label = tracked_labels.setdefault(int(item["tracking_id"]), label)
        labels.append(label)
    summary["unique_tracking_ids"] = sorted(tracked_labels)
    tracked_counts = Counter(tracked_labels.values())
    for label, count in Counter(labels).items():
        summary["object_counts"][label] = max(summary["object_counts"].get(label, 0), count, tracked_counts.get(label, 0))
    return movement


def detection_confidence(value):
    try:
        value = float(value)
    except (ValueError, TypeError):
        return MIN_CONFIDENCE
    return max(MIN_CONFIDENCE, min(1.0, value)) if math.isfinite(value) else MIN_CONFIDENCE


def box_iou(a, b):
    width = max(0, min(a['x'] + a['width'], b['x'] + b['width']) - max(a['x'], b['x']))
    height = max(0, min(a['y'] + a['height'], b['y'] + b['height']) - max(a['y'], b['y']))
    intersection = width * height
    union = a['width'] * a['height'] + b['width'] * b['height'] - intersection
    return intersection / union if union > 0 else 0.0


def suppress_duplicates(detections):
    """Keep the strongest box for heavily overlapping predictions, across labels."""
    kept = []
    for item in sorted(detections, key=lambda item: item['confidence'], reverse=True):
        if item['box']['width'] <= 0 or item['box']['height'] <= 0:
            continue
        if not any(box_iou(item['box'], other['box']) > DUPLICATE_IOU for other in kept):
            kept.append(item)
    return kept
