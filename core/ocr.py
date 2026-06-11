import re
from pathlib import Path


def load_ocr_reader():
    try:
        import easyocr

        return easyocr.Reader(
            ["en"],
            gpu=False,
            model_storage_directory=str(
                Path(__file__).resolve().parents[1] / "models" / "easyocr"
            ),
        )
    except Exception:
        return None


class PlateScanner:
    def __init__(self, reader=None):
        self.reader = reader
        self.backend = "EasyOCR" if reader else "Disabled"

    def scan(self, frame, vehicles):
        if self.reader is None:
            return []
        plates = []
        for vehicle in vehicles:
            box = vehicle["box"]
            x, y, w, h = box["x"], box["y"], box["width"], box["height"]
            crop = frame[max(0, y + h // 2):y + h, max(0, x):x + w]
            if crop.size == 0:
                continue
            for _, text, confidence in self.reader.readtext(crop):
                cleaned = re.sub(r"[^A-Z0-9]", "", text.upper())
                if 4 <= len(cleaned) <= 10 and confidence >= 0.3:
                    plates.append(
                        {"text": cleaned, "confidence": confidence * 100, "box": box}
                    )
                    break
        return plates
