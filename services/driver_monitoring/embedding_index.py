"""Private in-memory recognition index with revision/TTL refresh and backoff."""
import threading
import time


class DriverEmbeddingIndex:
    def __init__(self, store, ttl=300, retry_seconds=30, clock=time.monotonic):
        self.store, self.ttl, self.retry_seconds, self.clock = store, ttl, retry_seconds, clock
        self._lock = threading.RLock()
        self._profiles = None
        self._loaded_at = -float("inf")
        self._retry_at = 0
        self._revision = None
        self.refresh_count = 0

    def invalidate(self):
        with self._lock:
            self._profiles = None
            self._retry_at = 0

    def profiles(self):
        # Called exclusively by the inference worker. This lock never gates UI.
        with self._lock:
            now = self.clock()
            revision = getattr(self.store, "revision", 0)
            if revision != self._revision:
                self._profiles, self._retry_at = None, 0
                self._revision = revision
            if self._profiles is not None and now - self._loaded_at < self.ttl:
                return self._profiles
            if now < self._retry_at:
                raise RuntimeError("Private recognition index unavailable.")
            try:
                profiles = self.store.recognition_profiles()
                # Do not retain stale embeddings after a failed/expired refresh.
                self._profiles = profiles
                self._loaded_at = self.clock()
                self.refresh_count += 1
                return profiles
            except Exception:
                self._profiles = None
                self._retry_at = self.clock() + self.retry_seconds
                raise RuntimeError("Private recognition index unavailable.") from None
