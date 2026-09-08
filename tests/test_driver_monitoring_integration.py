import unittest
from unittest.mock import Mock, patch
from services.driver_monitoring.runtime import identity_metadata
from services.driver_monitoring.metadata import preserve_event_identity
from services.firebase_service import FirebaseService
from services.incident_service import IncidentService
from services.dual_session_service import build_video_metadata

IDENTITY=dict(driver_id="A",driver_identity_status="verified",driver_session_id="DS_A")


class DriverMetadataTests(unittest.TestCase):
    def test_firebase_optional_fields_and_no_biometrics(self):
        data=FirebaseService().build_video_document({"video_id":"video",**IDENTITY,"embedding":[1]*128})
        for k,v in IDENTITY.items():
            self.assertEqual(data[k],v)
        self.assertNotIn("embedding",data)
        legacy=FirebaseService().build_video_document({"video_id":"old"})
        self.assertNotIn("driver_id",legacy)

    def test_video_creation_never_requires_recognition(self):
        from services.video_service import VideoService
        with patch.object(VideoService,"__init__",return_value=None),patch.object(VideoService,"save"),patch("services.video_service.identity_metadata",return_value={"driver_id":None,"driver_identity_status":"unknown"}):
            record=VideoService().create_record("road",30,"640x480")
        self.assertEqual(record["recording_status"],"Recording")
        self.assertIsNone(record["driver_id"])

    def test_video_start_uses_verified_snapshot(self):
        from services.video_service import VideoService
        with patch.object(VideoService,"__init__",return_value=None),patch.object(VideoService,"save"),patch("services.video_service.identity_metadata",return_value=IDENTITY):
            self.assertEqual(VideoService().create_record("road",30,"640x480")["driver_id"],"A")

    def test_incident_uses_event_identity_over_video_start(self):
        video={"video_id":"video",**IDENTITY}
        event={"driver_id":None,"driver_identity_status":"unknown"}
        incident=IncidentService._incident(video,"test","low","test",event)
        self.assertIsNone(incident["driver_id"])

    def test_manual_incident_preserves_context_without_lookup(self):
        with patch.object(IncidentService,"__init__",return_value=None),patch.object(IncidentService,"list_incidents",return_value=[]),patch.object(IncidentService,"save_all"):
            incident=IncidentService().create_manual("video","test",driver_context=IDENTITY)
        self.assertEqual(incident["driver_id"],"A")

    def test_manual_incident_automatically_uses_current_driver_when_unlinked(self):
        with patch.object(IncidentService,"__init__",return_value=None),patch.object(IncidentService,"list_incidents",return_value=[]),patch.object(IncidentService,"save_all"),patch("services.incident_service.identity_metadata",return_value=IDENTITY) as context:
            incident=IncidentService().create_manual(None,"test")
        context.assert_called_once_with(None)
        self.assertEqual(incident["driver_id"],"A")

    def test_dual_video_metadata_backward_compatible(self):
        result=build_video_metadata({**IDENTITY,"session_id":"dual"},{"video_id":"rear"})
        self.assertEqual(result["driver_id"],"A")
        self.assertEqual(result["session_id"],"dual")

    def test_offline_processing_uses_only_recent_preceding_identity(self):
        events=[{"frame_number":n} for n in (1,30,45,100)]
        preserve_event_identity(events,[{"frame_number":30,**IDENTITY}],30)
        self.assertEqual([e["driver_id"] for e in events],[None,"A","A",None])

    def test_identity_allowlist_and_failure_fallback(self):
        self.assertEqual(identity_metadata({**IDENTITY,"embedding":"private"}),IDENTITY)
        with patch("services.driver_monitoring.runtime._runtime") as runtime:
            runtime.snapshot.side_effect=RuntimeError()
            self.assertIsNone(identity_metadata()["driver_id"])

    def test_gps_keeps_existing_link_and_optional_identity(self):
        from services.gps_service import GPSService
        gps=GPSService.__new__(GPSService)
        gps.store=Mock()
        gps.list_points=Mock(return_value=[])
        with patch("services.driver_monitoring.runtime.identity_metadata",return_value=IDENTITY):
            point=gps.add_point(1,2,video_id="video")
        self.assertEqual(point["video_id"],"video")
        self.assertEqual(point["driver_session_id"],"DS_A")
