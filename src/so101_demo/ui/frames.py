"""Latest sim frame for the big screen, as JPEG.

The frame is rendered on the runner's own thread through the runner's observer hook
(MuJoCo data is not shared across threads), every `every_n` control ticks, and the
server only hands out the last JPEG. Rendering is optional: without it `/frame.jpg`
answers 503 and the big screen shows the phase alone. Frame rate and coexistence with
Cosmos are measured in stage 3 (同時常駐 / 4c), not here.
"""

from __future__ import annotations

import io
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


def renderer_observer(store: FrameStore, width: int = 1280, height: int = 720, every_n: int = 2):
    """A `PlanRunner(observer=...)` factory that renders the fixed camera into `store`."""

    def make(sim):
        import mujoco  # after gl_env.prefer_gpu_on_wsl2() in the caller
        from PIL import Image

        from ..evidence.record import work_area_camera

        renderer = mujoco.Renderer(sim.model, height=height, width=width)
        camera = work_area_camera()
        tick = {"n": 0}

        def render() -> None:
            renderer.update_scene(sim.data, camera=camera)
            buf = io.BytesIO()
            Image.fromarray(renderer.render()).save(buf, format="JPEG", quality=85)
            store.put(buf.getvalue())

        render()

        def on_tick(_sim) -> None:
            tick["n"] += 1
            if tick["n"] % every_n == 0:
                render()

        return on_tick

    return make
