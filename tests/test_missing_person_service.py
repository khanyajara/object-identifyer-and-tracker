import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from PIL import Image
from services.missing_person_service import MissingPersonService


class MissingPersonTests(unittest.TestCase):
    def test_submission_persistence_and_review(self):
        with tempfile.TemporaryDirectory() as directory:
            service = MissingPersonService(directory)
            photo = BytesIO()
            Image.new("RGB", (20, 20)).save(photo, "PNG")
            report = service.submit(dict(name="Test Person", last_seen_location="Test location",
                last_seen_datetime="2026-09-14 10:00", contact_number="Test contact"), photo.getvalue())
            self.assertTrue(Path(report["photo_path"]).is_file())
            service.update_status(report["report_id"], "Located")
            self.assertEqual(MissingPersonService(directory).list_reports()[0]["status"], "Located")
            with self.assertRaises(ValueError):
                service.update_status(report["report_id"], "Invalid")

    def test_invalid_photo_does_not_create_report(self):
        with tempfile.TemporaryDirectory() as directory:
            service = MissingPersonService(directory)
            with self.assertRaises(ValueError):
                service.submit(dict(name="Test", last_seen_location="Location",
                    last_seen_datetime="2026-09-14", contact_number="Contact"), b"not an image")
            self.assertEqual(service.list_reports(), [])
