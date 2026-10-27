"""The stage-1 scene is built from the registry and every trial start is collision-free."""

import mujoco
import numpy as np
import pytest

from so101_demo.kinematics import workspace_contains
from so101_demo.plan import load_registry
from so101_demo.sim.scene import SceneConfig, load_scene, penetrations, set_object_pose, weld_name

REG = load_registry()


def test_scene_has_registered_bodies_and_inactive_welds():
    sim = load_scene(SceneConfig("sim-v1-simple", ("red_cube", "blue_cube")))
    for name in ("red_cube", "blue_cube", "box_a", "box_b"):
        assert mujoco.mj_name2id(sim.model, mujoco.mjtObj.mjOBJ_BODY, name) >= 0
    for o in ("red_cube", "blue_cube"):
        eq = mujoco.mj_name2id(sim.model, mujoco.mjtObj.mjOBJ_EQUALITY, weld_name(o))
        assert eq >= 0 and not sim.data.eq_active[eq]


def test_home_layout_has_no_penetration_and_rests():
    sim = load_scene(SceneConfig("sim-v1-simple", ("red_cube", "blue_cube")))
    assert penetrations(sim, min_depth_m=1e-3) == []
    sim.step(500)
    for o in ("red_cube", "blue_cube"):
        b = mujoco.mj_name2id(sim.model, mujoco.mjtObj.mjOBJ_BODY, o)
        assert abs(sim.data.xpos[b][2] - REG.object_rest_z(o)) < 0.001


@pytest.mark.parametrize("trial", REG.trials_0b, ids=[t.trial for t in REG.trials_0b])
def test_trial_start_is_inside_workspace_and_clear_of_trays(trial):
    sim = load_scene(SceneConfig("sim-v1-simple", (trial.object,)))
    set_object_pose(sim, trial.object, REG.start_xyz(trial))
    assert penetrations(sim, min_depth_m=1e-3) == []
    assert workspace_contains(REG.start_xyz(trial))
    half = REG.objects[trial.object].size_m / 2
    for d in REG.destinations.values():
        gap_y = abs(trial.start_xy_m[1] - d.center_xy_m[1]) - d.outer_half_m - half
        gap_x = abs(trial.start_xy_m[0] - d.center_xy_m[0]) - d.outer_half_m - half
        assert max(gap_x, gap_y) >= 0.010  # fixed 10 mm clearance


def test_set_object_pose_zeroes_velocity():
    sim = load_scene(SceneConfig("sim-v1-simple", ("red_cube",)))
    set_object_pose(sim, "red_cube", (0.19, 0.0, 0.05), yaw_deg=30)
    b = mujoco.mj_name2id(sim.model, mujoco.mjtObj.mjOBJ_BODY, "red_cube")
    np.testing.assert_allclose(sim.data.xpos[b], [0.19, 0.0, 0.05], atol=1e-9)
    assert np.all(sim.data.qvel[-6:] == 0)
