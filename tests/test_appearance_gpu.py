"""Issue #10 rendering checks (GPU machine): visibility from the fixed camera on all four
fixtures, projection against segmentation, 1080p frame with labels, glyph height."""

import mujoco
import numpy as np
import pytest

from so101_demo.evidence.appearance import (
    HEIGHT,
    MIN_FRAME_MARGIN_PX,
    MIN_PX,
    WIDTH,
    VisibilitySampler,
    fixtures,
    visibility_verdict,
)
from so101_demo.evidence.overlay import (
    MIN_GLYPH_PX,
    LabelOverlay,
    glyph_height_px,
    margin_px,
    project,
)
from so101_demo.evidence.record import VideoRecorder, work_area_camera
from so101_demo.execution.runner import PlanRunner, approve_for_development, parse_plan
from so101_demo.plan import load_registry

pytestmark = pytest.mark.gpu


def test_every_fixture_keeps_cubes_trays_and_jaw_visible_from_the_fixed_camera():
    reg = load_registry()
    runner = PlanRunner("sim-v1-simple", styled=True)
    for f in fixtures():
        plan = approve_for_development(
            parse_plan(f.read_text(encoding="utf-8")), f.stem, approve_fixed_fixture=True
        )
        sampler = VisibilitySampler(runner.sim, reg, work_area_camera())
        runner._observer_cb = sampler
        try:
            rec = runner.submit(plan)
        finally:
            runner._observer_cb = None
            sampler.close()
        assert rec.outcome == "success", (f.stem, rec.outcome, rec.reason)
        assert len(sampler.samples) >= 40, f.stem  # ~10 s at 5 Hz
        other = next(o for o in reg.objects if o != plan.plan.target)
        verdict = visibility_verdict(sampler.minima(), plan.plan.target, other)
        bad = {k: v for k, v in verdict.items() if not v[2]}
        assert not bad, (f.stem, bad, MIN_PX)
        # both trays stay inside the frame at the ADR 0008 layout (Issue #20)
        trays = {k: sampler.margins.get(k, -1) for k in reg.destinations}
        assert all(m >= MIN_FRAME_MARGIN_PX for m in trays.values()), (f.stem, trays)


def test_projection_matches_the_segmentation_centroid_of_each_cube(tmp_path):
    reg = load_registry()
    runner = PlanRunner("sim-v1-simple", styled=True)
    rec = VideoRecorder(runner.sim, tmp_path / "f.mp4", width=WIDTH, height=HEIGHT, fps=30)
    seg_r = mujoco.Renderer(runner.sim.model, HEIGHT, WIDTH)
    seg_r.enable_segmentation_rendering()
    try:
        rec.frame()
        seg_r.update_scene(runner.sim.data, camera=rec.camera)
        seg = seg_r.render()[..., 0]
        for oid in reg.objects:
            gid = mujoco.mj_name2id(runner.sim.model, mujoco.mjtObj.mjOBJ_GEOM, f"{oid}_geom")
            ys, xs = np.nonzero(seg == gid)
            u, v = project(rec.renderer.scene, WIDTH, HEIGHT, runner.sim.data.body(oid).xpos)
            # the visible face centroid sits within the cube's own projected size
            assert abs(xs.mean() - u) < 25 and abs(ys.mean() - v) < 25, oid
    finally:
        rec.close()
        seg_r.close()


def test_labelled_1080p_frame_has_readable_glyphs(tmp_path):
    reg = load_registry()
    runner = PlanRunner("sim-v1-simple", styled=True)
    overlay = LabelOverlay(reg, WIDTH, HEIGHT)
    rec = VideoRecorder(
        runner.sim, tmp_path / "f.mp4", width=WIDTH, height=HEIGHT, fps=30, decorate=overlay
    )
    try:
        img = rec.frame()
    finally:
        rec.close()
    assert img.shape == (HEIGHT, WIDTH, 3)
    boxes = overlay.boxes_by_frame[0]
    assert set(boxes) == set(reg.objects) | set(reg.destinations)
    for k, b in boxes.items():
        assert glyph_height_px(img, b) >= MIN_GLYPH_PX, k
        assert margin_px(b, WIDTH, HEIGHT) >= MIN_FRAME_MARGIN_PX, (k, b)
