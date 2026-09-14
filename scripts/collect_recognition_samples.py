"""Collect consented, labelled stationary face-test images into private local storage."""
import argparse
import csv
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    import cv2
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--camera", type=int, required=True)
    parser.add_argument("--person", required=True, help="Pseudonymous person ID, not a name")
    parser.add_argument("--session", required=True, help="Unique capture session ID")
    parser.add_argument("--split", choices=("enroll", "calibration", "test"), required=True)
    parser.add_argument("--samples", type=int, default=25)
    parser.add_argument("--output", type=Path, default=Path("data/driver_training"))
    args = parser.parse_args()
    if any(not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", value) for value in (args.person, args.session)) or not 5 <= args.samples <= 100:
        parser.error("Use simple person/session IDs and 5–100 samples")
    print("Close Roadwatch first. Park safely. This collector SAVES camera images locally.")
    print("Use one consenting person, avoid bystanders, and turn slightly between captures.")
    if input("Does the person consent to these private recognition-test images? Type yes: ").strip().lower() != "yes":
        raise SystemExit("Cancelled")
    folder = args.output / args.split / args.person / args.session
    if folder.exists():
        parser.error("This session folder already exists; choose a new session ID")
    manifest = args.output / "manifest.csv"
    existing = []
    if manifest.exists():
        with manifest.open(newline="", encoding="utf-8") as stream:
            existing = list(csv.DictReader(stream))
        if any(r["session_id"] == args.session and r["split"] != args.split for r in existing):
            parser.error("A session cannot cross dataset splits")
    capture = cv2.VideoCapture(args.camera)
    rows = []
    try:
        if not capture.isOpened():
            raise RuntimeError("Camera unavailable")
        folder.mkdir(parents=True)
        began = last = time.monotonic()
        while len(rows) < args.samples and time.monotonic() - began < 180:
            ok, frame = capture.read()
            if not ok:
                raise RuntimeError("Camera stopped delivering frames")
            cv2.imshow("Recognition samples - SPACE to save, ESC to stop", frame)
            key = cv2.waitKey(1) & 0xff
            if key == 27:
                break
            if key == 32 and time.monotonic() - last >= .75:
                path = folder / f"{len(rows):03d}.jpg"
                if not cv2.imwrite(str(path), frame):
                    raise RuntimeError("Image could not be saved")
                rows.append({"path": path.relative_to(args.output).as_posix(), "person_id": args.person, "session_id": args.session, "split": args.split})
                last = time.monotonic()
                print(f"Saved {len(rows)}/{args.samples}")
    finally:
        capture.release()
        cv2.destroyAllWindows()
        if rows:
            temporary = manifest.with_suffix(".csv.tmp")
            with temporary.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=["path", "person_id", "session_id", "split"])
                writer.writeheader()
                writer.writerows(existing + rows)
            temporary.replace(manifest)
    print(f"Saved {len(rows)} images. Do not split adjacent frames between calibration and test sessions.")


if __name__ == "__main__":
    main()
