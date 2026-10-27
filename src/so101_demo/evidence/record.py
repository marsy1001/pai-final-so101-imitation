"""Record a trial as an MP4 from a fixed camera (GPU-machine evidence).

Frames come from `mujoco.Renderer` (offscreen; set `MUJOCO_GL=egl` on a machine
without a display) and go straight into an `ffmpeg` process on stdin, so no video
library is added to the project. One file per trial, H.264, 640x480 at 25 fps: a
10 s trial is well under the 10 MB per-file limit for evidence clips.

The recorder is an observer of `execution.trial.run_trial`: it is called after every
control tick and renders whenever sim time has advanced by one frame period.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

import mujoco
import numpy as np

from ..sim.model import So101Sim

WIDTH, HEIGHT, FPS = 640, 480, 25
# screen recording (2026-09-19): 1080p, frames on every 2nd control tick (CONTROL_DT_S = 0.02 s)
VIDEO_WIDTH, VIDEO_HEIGHT, VIDEO_FPS = 1920, 1080, 25
# free camera on the work area (2026-09-19): straight in front of the base,
# low and close, so both cubes, both trays and the jaws stay visible in every phase of
# all four fixtures (segmentation search over azimuth 110-230, elevation -20..-55,
# distance 0.45-0.7; the earlier 145/-35/0.6 hid the non-target cube behind the arm)
LOOKAT = (0.19, 0.0, 0.08)
DISTANCE_M = 0.50
AZIMUTH_DEG = 180.0
ELEVATION_DEG = -22.0


def work_area_camera() -> mujoco.MjvCamera:
    cam = mujoco.MjvCamera()
    cam.type = mujoco.mjtCamera.mjCAMERA_FREE
    cam.lookat[:] = LOOKAT
    cam.distance = DISTANCE_M
    cam.azimuth = AZIMUTH_DEG
    cam.elevation = ELEVATION_DEG
    return cam


def brighten(model: mujoco.MjModel) -> None:
    """Stronger headlight for legible evidence. `mjVisual` only: no effect on physics
    (the pinned MJCF has no lights of its own and renders dark)."""
    model.vis.headlight.ambient[:] = (0.45, 0.45, 0.45)
    model.vis.headlight.diffuse[:] = (0.9, 0.9, 0.9)
    model.vis.headlight.specular[:] = (0.2, 0.2, 0.2)


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


NVENC = "h264_nvenc"
X264 = "libx264"


def h264_encoder(encoder: str = X264) -> str:
    """Resolve `encoder`: `auto` = NVENC when ffmpeg lists it, else libx264.

    Measured 2026-09-19: libx264's threads compete with the D3D12 render path on
    the GPU laptop and slow every frame (render 20 -> 28 ms, pipeline 27 fps); NVENC
    keeps the render at 15 ms (39 fps). The 0b / 4a evidence stays on libx264.
    """
    if encoder != "auto":
        return encoder
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True, check=False
    ).stdout
    return NVENC if f" {NVENC} " in out else X264


Decorator = Callable[[np.ndarray, So101Sim, "VideoRecorder"], np.ndarray]


class VideoRecorder:
    """Render `sim` into `path` at `fps`. Use as a context manager or call close().

    Defaults are the small evidence format (640x480, 25 fps). The screen format
    (2026-09-19) passes 1920x1080, 30 fps and a `decorate` hook that draws labels on
    each frame; the model must then have a large enough offscreen buffer
    (`sim.appearance.style_spec`). Wall-clock and sim time of every frame are kept
    for the frame-rate evidence (`fps_stats`).
    """

    def __init__(
        self,
        sim: So101Sim,
        path: Path,
        *,
        crf: int = 28,
        width: int = WIDTH,
        height: int = HEIGHT,
        fps: int = FPS,
        decorate: Decorator | None = None,
        brighten_headlight: bool = True,
        preset: str = "medium",
        renderer: mujoco.Renderer | None = None,
        encoder: str = X264,
    ) -> None:
        if not ffmpeg_available():
            raise RuntimeError("ffmpeg not found on PATH; needed to write the trial video")
        self.sim = sim
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if brighten_headlight:
            brighten(sim.model)
        self.width, self.height, self.fps = width, height, fps
        self.decorate = decorate
        # One GL context per process: every extra mujoco.Renderer slows all later frames
        # (2026-09-19: 35 -> 29 -> 27 fps over three recorders). Callers that record
        # several videos pass one shared renderer of the same size.
        self._owns_renderer = renderer is None
        self.renderer = renderer or mujoco.Renderer(sim.model, height, width)
        if (self.renderer.height, self.renderer.width) != (height, width):
            raise ValueError("shared renderer size does not match the video size")
        self.camera = work_area_camera()
        self.frames = 0
        self.last_frame: np.ndarray | None = None  # the most recent (decorated) frame
        self.wall_times: list[float] = []
        self.sim_times: list[float] = []
        self._next_t = -np.inf
        self.encoder = h264_encoder(encoder)
        codec = (
            ["-c:v", NVENC, "-preset", "p4", "-cq", str(crf)]
            if self.encoder == NVENC
            else ["-c:v", X264, "-preset", preset, "-crf", str(crf)]
        )
        self._proc = subprocess.Popen(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "-s",
                f"{width}x{height}",
                "-r",
                str(fps),
                "-i",
                "-",
                *codec,
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                str(self.path),
            ],
            stdin=subprocess.PIPE,
        )

    def frame(self) -> np.ndarray:
        """Render one frame now and append it (also returned, for tests)."""
        self.renderer.update_scene(self.sim.data, camera=self.camera)
        img = self.renderer.render()
        if self.decorate is not None:
            img = self.decorate(img, self.sim, self)
        assert self._proc.stdin is not None
        self._proc.stdin.write(np.ascontiguousarray(img, dtype=np.uint8).tobytes())
        self.last_frame = img
        self.frames += 1
        self.wall_times.append(time.perf_counter())
        self.sim_times.append(float(self.sim.data.time))
        return img

    def __call__(self, sim: So101Sim) -> None:
        """Observer hook: one frame per 1/fps of sim time."""
        if sim.data.time >= self._next_t:
            self.frame()
            self._next_t = sim.data.time + 1.0 / self.fps - 1e-9

    def fps_stats(self) -> dict[str, float]:
        """Effective frame rate over the recorded interval, measured on the wall clock:
        `effective_fps` = (frames - 1) / (last - first), `max_interval_s` = longest gap
        between two consecutive frames. Both are fixed (measured 2026-09-19)."""
        if len(self.wall_times) < 2:
            return {"effective_fps": 0.0, "max_interval_s": float("inf"), "frames": self.frames}
        w = np.diff(np.asarray(self.wall_times))
        return {
            "effective_fps": float(
                (len(self.wall_times) - 1) / (self.wall_times[-1] - self.wall_times[0])
            ),
            "max_interval_s": float(w.max()),
            "mean_interval_s": float(w.mean()),
            "frames": self.frames,
            "sim_span_s": float(self.sim_times[-1] - self.sim_times[0]),
        }

    def close(self) -> int:
        """Finish the file; returns its size in bytes."""
        if self._proc.stdin is not None:
            self._proc.stdin.close()
        rc = self._proc.wait()
        if self._owns_renderer:
            self.renderer.close()
        if rc != 0:
            raise RuntimeError(f"ffmpeg exited with {rc} while writing {self.path}")
        return self.path.stat().st_size

    def __enter__(self) -> VideoRecorder:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
