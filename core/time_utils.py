"""Shared timestamp formatting for persisted records."""
from datetime import datetime, timezone


def utc_now():
    return datetime.now(timezone.utc).isoformat()
