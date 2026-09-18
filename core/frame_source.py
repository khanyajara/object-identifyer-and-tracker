"""Capture boundary: owned numpy uint8 BGR H x W x 3 frames.

CameraChannel is the single reader/recorder owner. Preview and independent AI
consumers read its latest snapshot rather than competing for source.read().
"""
from typing import Protocol, runtime_checkable


@runtime_checkable
class CameraSource(Protocol):
    def read(self): ...
    def isOpened(self): ...
    def get(self, key): ...
    def release(self): ...


class LocalOpenCVCameraSource:
    """Adapt an already configured local OpenCV handle to CameraSource."""
    def __init__(self, capture):
        self.capture = capture

    def read(self):
        return self.capture.read()

    def isOpened(self):
        return self.capture.isOpened()

    def get(self, key):
        return self.capture.get(key)

    def release(self):
        self.capture.release()
