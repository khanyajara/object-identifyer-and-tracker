import cv2


def annotate_frame(frame, objects, plates, movement_detected, camera_fps):
    for index, item in enumerate(objects, start=1):
        box = item["box"]
        x, y = int(box["x"]), int(box["y"])
        width, height = int(box["width"]), int(box["height"])
        color = (40, 209, 124)
        label = item["label"]
        if item.get("tracking_id") is not None:
            label += f' ID {item["tracking_id"]}'
        else:
            label += f" DET {index}"
        label += f' {item["confidence"] / 100:.2f}'
        cv2.rectangle(frame, (x, y), (x + width, y + height), color, 4)
        (text_width, text_height), baseline = cv2.getTextSize(
            label, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2
        )
        text_top = max(0, y - text_height - baseline - 12)
        cv2.rectangle(
            frame,
            (x, text_top),
            (x + text_width + 14, text_top + text_height + baseline + 12),
            color,
            -1,
        )
        cv2.putText(
            frame,
            label,
            (x + 7, text_top + text_height + 4),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (4, 18, 31),
            2,
            cv2.LINE_AA,
        )
    for plate in plates:
        box = plate.get("box", {})
        x, y = int(box.get("x", 0)), int(box.get("y", 0))
        width = int(box.get("width", 0))
        height = int(box.get("height", 0))
        confidence = float(plate.get("confidence", 0)) / 100
        label = f'plate {plate.get("text", "unknown")} {confidence:.2f}'
        cv2.rectangle(
            frame,
            (x, y),
            (x + width, y + height),
            (244, 185, 66),
            3,
        )
        cv2.putText(
            frame, label, (x, max(20, y - 8)),
            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (244, 185, 66), 2
        )
    status = f"FPS {camera_fps:.1f}"
    if movement_detected:
        status += " | MOVEMENT"
    cv2.rectangle(frame, (10, 8), (310, 44), (4, 18, 31), -1)
    cv2.putText(
        frame, status, (18, 34), cv2.FONT_HERSHEY_SIMPLEX,
        0.75, (255, 255, 255), 2, cv2.LINE_AA
    )
    return frame
