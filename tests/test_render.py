"""Rendering evidence path (GPU machine): offscreen frames and the MP4 writer.

Marked `gpu`: needs an OpenGL context (set `MUJOCO_GL=egl` without a display) and
`ffmpeg` on PATH. Deselected by default; run with `uv run pytest -q -m gpu`.
"""

import subprocess

import numpy as np
import pytest

from so101_demo.evidence.record import FPS, HEIGHT, WIDTH, VideoRecorder, ffmpeg_available
from so101_demo.execution.runner import PlanRunner, approve_for_development, parse_plan
from so101_demo.execution.trial import run_trial
from so101_demo.plan import load_registry
from so101_demo.plan.paths import FIXTURES_4A_DIR
from so101_demo.sim.scene import SceneConfig, load_scene

pytestmark = pytest.mark.gpu


def test_offscreen_frame_shows_the_scene(tmp_path):
    if not ffmpeg_available():
        pytest.fail("ffmpeg not on PATH")
    sim = load_scene(SceneConfig("sim-v1-simple", ("red_cube",)))
    with VideoRecorder(sim, tmp_path / "frame.mp4") as rec:
        img = rec.frame()
    assert img.shape == (HEIGHT, WIDTH, 3) and img.dtype == np.uint8
    # not a blank buffer: the table, the arm and the red cube differ in colour
    assert img.std() > 10.0
    red = img[..., 0].astype(int) - img[..., 1].astype(int)
    assert red.max() > 60, "the red cube should be visible from the work-area camera"


def test_trial_video_has_the_expected_frame_count_and_size(tmp_path):
    reg = load_registry()
    spec = reg.trials_0b[0]
    holder = {}

    def observer(sim):
        holder["rec"] = VideoRecorder(sim, tmp_path / "trial.mp4")
        return holder["rec"]

    res = run_trial(spec, "sim-v1-simple", reg, observer=observer)
    size = holder["rec"].close()
    # one frame per 1/FPS of sim time over reset settle + trial (+-1 frame)
    expected = (1.0 + res.sim_time_s) * FPS
    assert abs(holder["rec"].frames - expected) <= 2
    assert 0 < size < 10 * 1024 * 1024
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-count_frames",
            "-show_entries",
            "stream=nb_read_frames,width,height",
            "-of",
            "csv=p=0",
            str(tmp_path / "trial.mp4"),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    w, h, n = probe.stdout.strip().split(",")
    assert (int(w), int(h)) == (WIDTH, HEIGHT)
    assert int(n) == holder["rec"].frames


def test_runner_video_covers_the_running_interval_of_a_fixture(tmp_path):
    """The 4a acceptance path: a fixture through PlanRunner with the recorder as observer."""
    holder = {}

    def observer(sim):
        holder["rec"] = VideoRecorder(sim, tmp_path / "fixture.mp4")
        return holder["rec"]

    runner = PlanRunner("sim-v1-simple", observer=observer)
    fixture = sorted(FIXTURES_4A_DIR.glob("*.json"))[0]
    plan = approve_for_development(
        parse_plan(fixture.read_text(encoding="utf-8")), fixture.stem, approve_fixed_fixture=True
    )
    rec = runner.submit(plan)
    size = holder["rec"].close()
    assert rec.outcome == "success" and rec.observer_error is None
    # frames only while the plan runs (the reset is physics-only, no observer): +-1 frame
    assert abs(holder["rec"].frames - rec.sim_time_s * FPS) <= 2
    assert 0 < size < 10 * 1024 * 1024
