"""Shared object confidence and overlapping-box policy."""

import math

MIN_CONFIDENCE = 0.88
DUPLICATE_IOU = 0.70


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
