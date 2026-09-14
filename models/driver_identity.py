"""Public driver session data; never contains biometric embeddings."""
from dataclasses import dataclass, field
from uuid import uuid4

from core.time_utils import utc_now


@dataclass
class DriverSession:
    session_id: str = field(default_factory=lambda: "DS_" + uuid4().hex)
    driver_id: str | None = None
    identity_status: str = "no_face"
    started_at: str = field(default_factory=utc_now)
    last_seen_at: str | None = None
    verified_at: str | None = None
    vehicle_id: str | None = None
    video_id: str | None = None
    trip_id: str | None = None
    recognition_confidence: float = 0.0
    driver_display_name: str = "Unknown Driver"
