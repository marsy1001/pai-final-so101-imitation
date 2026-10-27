"""MuJoCo loads the pinned SO-101 model and steps it without rendering."""

import mujoco
import numpy as np

from so101_demo.assets import SO101_REVISION
from so101_demo.frames import GRIPPER_JOINT, JOINT_NAMES
from so101_demo.sim import So101Sim


def test_pinned_revision_is_recorded():
    assert SO101_REVISION.startswith("eecbe3e")


def test_model_loads_and_steps_100_times():
    sim = So101Sim(with_scene=True)
    sim.step(100)
    assert sim.state_is_finite()
    assert sim.data.time > 0


def test_bare_model_loads_and_steps_100_times():
    sim = So101Sim(with_scene=False)
    sim.step(100)
    assert sim.state_is_finite()


def test_joints_are_matched_by_name(sim: So101Sim):
    limits = sim.joint_limits_rad()
    assert set(limits) == {*JOINT_NAMES, GRIPPER_JOINT}
    for lo, hi in limits.values():
        assert lo < hi


def test_mjcf_uses_radians(sim: So101Sim):
    # compiler angle="radian": the widest range must still be below 2*pi.
    assert np.all(np.abs(sim.arm_limits_rad()) < 2 * np.pi)
    assert sim.model.opt.timestep > 0
    assert isinstance(sim.model, mujoco.MjModel)
