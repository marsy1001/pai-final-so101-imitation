"""Latest sim frame for the big screen, as JPEG.

The frame is rendered on the runner's own thread (MuJoCo data and the GL context are not
shared across threads) by `ui.recording.ScreenRecorder`, and the server only hands out
the last JPEG. Rendering is optional: without it `/frame.jpg` answers 503 and the big
screen shows the phase alone.
"""

from __future__ import annotations

import threading


class FrameStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jpeg: bytes | None = None
        self.count = 0

    def put(self, jpeg: bytes) -> None:
        with self._lock:
            self._jpeg = jpeg
            self.count += 1

    def latest(self) -> bytes | None:
        with self._lock:
            return self._jpeg
