"""The big screen's frames and the per-run 1080p recording, on the runner's thread (#3).

One `mujoco.Renderer` of the video size (1920x1080) is created on the runner thread by
the observer factory and used for both outputs: the big screen gets the same frame
scaled to 1280x720 as JPEG (`FrameStore`), every `every_n` control ticks; with a record
directory, every run is written to `<dir>/<run_id>-<target>-<destination>.mp4` by the
screen recorder of `evidence.record` (25 fps of sim time, labels, NVENC when ffmpeg has
it), active during the run only (`PlanRunner` calls the observer while a plan runs, not
during the reset). Recording never decides the run: a recorder that cannot be started
lets the run go ahead without a video, a frame failure stays in
`RunRecord.observer_error` (docs/execution-runner.md), a file that cannot be finished is
noted in `Recording.error`; the `RunRecord` is returned in every case.

The wall-clock frame rate of the recording is measured (`Recording.effective_fps`); a run
under 25 fps is recorded as such, not failed (Issue #3).
"""

from __future__ import annotations

import io
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..evidence.gl_env import gl_renderer_name
from ..evidence.record import (
    VIDEO_FPS,
    VIDEO_HEIGHT,
    VIDEO_WIDTH,
    VideoRecorder,
    h264_encoder,
    work_area_camera,
)
from ..execution.runner import ApprovedPlan, PlanRunner, RunRecord
from .frames import FrameStore

SCREEN_WIDTH, SCREEN_HEIGHT = 1280, 720
X264_PRESET = "veryfast"  # libx264 fallback only; NVENC is used when ffmpeg has it
# control ticks between two big-screen JPEGs: 5 x 0.02 s = 10 Hz, the rate the server pushes
# the view at (PUSH_INTERVAL_S); more would cost render time for frames nobody fetches
EVERY_N_TICKS = 5


@dataclass(frozen=True)
class Recording:
    """One run's video file and its measured frame rate."""

    path: str | None  # file name in the record directory; None when nothing was written
    bytes: int | None
    frames: int
    effective_fps: float | None  # None with fewer than two frames
    max_interval_s: float | None
    mean_interval_s: float | None
    sim_span_s: float | None
    encoder: str | None
    error: str | None  # why there is no video, or the recorder's first failure in the run

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class ScreenRecorder:
    """`observer` goes to `PlanRunner(observer=..., styled=True)`; `run` replaces
    `runner.submit` and must be called on the runner thread (`RunnerThread.run`)."""

    def __init__(
        self, frames: FrameStore, record_dir: Path | None = None, *, every_n: int = EVERY_N_TICKS
    ) -> None:
        self.frames = frames
        self.record_dir = Path(record_dir) if record_dir is not None else None
        self.every_n = every_n
        self.recordings: dict[str, Recording] = {}  # run id -> its video
        self.gl_name: str | None = None
        self.render_errors: list[str] = []  # idle-frame failures after a run (display only)
        # resolved once: NVENC when ffmpeg lists it, else libx264 (an ffmpeg probe per run
        # would sit on the runner thread at the start of every run)
        self.encoder: str | None = None
        if self.record_dir is not None:
            try:
                self.encoder = h264_encoder("auto")
            except OSError:  # no ffmpeg: every run is then recorded as "recorder not started"
                self.encoder = None
        self._lock = threading.Lock()
        self._sim = None
        self._renderer = None
        self._camera = None
        self._active: VideoRecorder | None = None
        self._tick = 0
        if self.record_dir is not None:
            self.record_dir.mkdir(parents=True, exist_ok=True)

    # --- on the runner thread ------------------------------------------------------
    def observer(self, sim):
        import mujoco  # after gl_env.prefer_gpu_on_wsl2() in the caller

        self._sim = sim
        self._renderer = mujoco.Renderer(sim.model, VIDEO_HEIGHT, VIDEO_WIDTH)
        self._camera = work_area_camera()
        self.gl_name = gl_renderer_name()  # while the context is alive
        self.render_idle()

        def on_tick(s) -> None:
            self._tick += 1
            rec = self._active
            if rec is not None:
                rec(s)  # one frame per 1/fps of sim time; the frame carries the labels
                if self._tick % self.every_n == 0 and rec.last_frame is not None:
                    self._publish(rec.last_frame)
            elif self._tick % self.every_n == 0:
                self.render_idle()

        return on_tick

    def _render_idle_safely(self) -> str | None:
        """`render_idle` after a run: a failure here is returned, never raised, so the
        run's record (outcome, commands sent) is not lost to a display problem."""
        try:
            self.render_idle()
        except Exception as e:  # noqa: BLE001 - display only
            self.render_errors.append(f"{type(e).__name__}: {e}")
            return f"idle frame not rendered: {type(e).__name__}: {e}"
        return None

    def render_idle(self) -> None:
        """One frame of the scene as it is now (idle, or after a run's reset)."""
        if self._renderer is None:
            return
        self._renderer.update_scene(self._sim.data, camera=self._camera)
        self._publish(self._renderer.render())

    def _publish(self, img) -> None:
        from PIL import Image

        pil = Image.fromarray(img)
        if pil.size != (SCREEN_WIDTH, SCREEN_HEIGHT):
            pil = pil.resize((SCREEN_WIDTH, SCREEN_HEIGHT), Image.BILINEAR)
        buf = io.BytesIO()
        pil.save(buf, format="JPEG", quality=85)
        self.frames.put(buf.getvalue())

    def run(self, runner: PlanRunner, approved: ApprovedPlan) -> RunRecord:
        """`runner.submit(approved)` with the recording around it."""
        if self.record_dir is None:
            try:
                return runner.submit(approved)
            finally:
                self._render_idle_safely()
        from ..evidence.overlay import LabelOverlay

        plan = approved.plan
        path = self.record_dir / f"{approved.reference}-{plan.target}-{plan.destination}.mp4"
        try:
            recorder = VideoRecorder(
                self._sim,
                path,
                width=VIDEO_WIDTH,
                height=VIDEO_HEIGHT,
                fps=VIDEO_FPS,
                decorate=LabelOverlay(runner.registry, VIDEO_WIDTH, VIDEO_HEIGHT),
                brighten_headlight=False,
                preset=X264_PRESET,
                renderer=self._renderer,
                encoder=self.encoder or "auto",
            )
        except Exception as e:  # noqa: BLE001 - no ffmpeg, no directory: run without a video
            recorder, error = None, f"recorder not started: {type(e).__name__}: {e}"
        else:
            error = None
        self._active = recorder
        size = None
        try:
            rec = runner.submit(approved)
        finally:
            self._active = None
            if recorder is not None:
                try:
                    size = recorder.close()
                except Exception as e:  # noqa: BLE001 - the run is already decided
                    error = f"video not finished: {type(e).__name__}: {e}"
            render_error = self._render_idle_safely()
            if render_error:
                error = f"{error}; {render_error}" if error else render_error
        stats = recorder.fps_stats() if recorder is not None else {"frames": 0}
        frames = int(stats["frames"])
        written = recorder is not None and size is not None and frames > 0
        recording = Recording(
            path.name if written else None,
            size if written else None,
            frames,
            _finite(stats.get("effective_fps")) if frames > 1 else None,
            _finite(stats.get("max_interval_s")) if frames > 1 else None,
            _finite(stats.get("mean_interval_s")) if frames > 1 else None,
            _finite(stats.get("sim_span_s")) if frames > 1 else None,
            recorder.encoder if recorder is not None else None,
            error or rec.observer_error or (None if written else "no frame was recorded"),
        )
        if written:
            rec.video_path, rec.video_bytes = path.name, size
        with self._lock:
            self.recordings[approved.reference] = recording
        return rec

    def recording_of(self, run_id: str | None) -> Recording | None:
        with self._lock:
            return self.recordings.get(run_id) if run_id else None


def _finite(x) -> float | None:
    """A float for JSON: None for inf / nan (json.dumps would write `Infinity`)."""
    if x is None:
        return None
    x = float(x)
    return x if x == x and abs(x) != float("inf") else None
