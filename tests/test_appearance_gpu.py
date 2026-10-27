"""Issue #10 rendering checks (GPU machine): projection against segmentation, 1080p frame
with labels, glyph height."""

import mujoco
import numpy as np
import pytest

from so101_demo.evidence.overlay import (
    MIN_GLYPH_PX,
    LabelOverlay,
    glyph_height_px,
    margin_px,
    project,
)
from so101_demo.evidence.record import VENUE_HEIGHT, VENUE_WIDTH, VideoRecorder
from so101_demo.execution.runner import PlanRunner
from so101_demo.plan import load_registry

pytestmark = pytest.mark.gpu
MIN_FRAME_MARGIN_PX = 1  # every label box stays inside the 1080p frame


def test_projection_matches_the_segmentation_centroid_of_each_cube(tmp_path):
    reg = load_registry()
    runner = PlanRunner("sim-v1-simple", styled=True)
    rec = VideoRecorder(
        runner.sim, tmp_path / "f.mp4", width=VENUE_WIDTH, height=VENUE_HEIGHT, fps=30
    )
    seg_r = mujoco.Renderer(runner.sim.model, VENUE_HEIGHT, VENUE_WIDTH)
    seg_r.enable_segmentation_rendering()
    try:
        rec.frame()
        seg_r.update_scene(runner.sim.data, camera=rec.camera)
        seg = seg_r.render()[..., 0]
        for oid in reg.objects:
            gid = mujoco.mj_name2id(runner.sim.model, mujoco.mjtObj.mjOBJ_GEOM, f"{oid}_geom")
            ys, xs = np.nonzero(seg == gid)
            u, v = project(
                rec.renderer.scene, VENUE_WIDTH, VENUE_HEIGHT, runner.sim.data.body(oid).xpos
            )
            # the visible face centroid sits within the cube's own projected size
            assert abs(xs.mean() - u) < 25 and abs(ys.mean() - v) < 25, oid
    finally:
        rec.close()
        seg_r.close()


def test_labelled_1080p_frame_has_readable_glyphs(tmp_path):
    reg = load_registry()
    runner = PlanRunner("sim-v1-simple", styled=True)
    overlay = LabelOverlay(reg, VENUE_WIDTH, VENUE_HEIGHT)
    rec = VideoRecorder(
        runner.sim,
        tmp_path / "f.mp4",
        width=VENUE_WIDTH,
        height=VENUE_HEIGHT,
        fps=30,
        decorate=overlay,
    )
    try:
        img = rec.frame()
    finally:
        rec.close()
    assert img.shape == (VENUE_HEIGHT, VENUE_WIDTH, 3)
    boxes = overlay.boxes_by_frame[0]
    assert set(boxes) == set(reg.objects) | set(reg.destinations)
    for k, b in boxes.items():
        assert glyph_height_px(img, b) >= MIN_GLYPH_PX, k
        assert margin_px(b, VENUE_WIDTH, VENUE_HEIGHT) >= MIN_FRAME_MARGIN_PX, (k, b)
