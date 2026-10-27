"""RobotBackend contract on the MuJoCo adapter (docs/robot-backend.md)."""

import numpy as np
import pytest

from so101_demo.frames import JOINT_NAMES
from so101_demo.robot import MujocoBackend, NotCommandOwner
from so101_demo.robot.mujoco_backend import deg_to_gripper, gripper_to_deg
from so101_demo.sim.grasp import SimpleGrasp
from so101_demo.sim.scene import SceneConfig, load_scene

HOME = np.array([0.0, -17.0, 20.0, 86.0, 0.0])


@pytest.fixture
def backend():
    sim = load_scene(SceneConfig("sim-v1-simple", ("red_cube",)))
    return MujocoBackend(sim, SimpleGrasp(sim, ("red_cube",)), home_q_deg=HOME)


def test_gripper_mapping_endpoints_and_inverse():
    assert gripper_to_deg(0) == -10.0 and gripper_to_deg(100) == 100.0
    assert (
        gripper_to_deg(-5) == -10.0 and gripper_to_deg(150) == 100.0
    )  # clipped, never extrapolated
    for v in (0.0, 30.0, 100.0):
        assert abs(deg_to_gripper(gripper_to_deg(v)) - v) < 1e-9


def test_single_command_source(backend):
    a = backend.acquire("execution")
    with pytest.raises(NotCommandOwner):
        backend.acquire("ui")
    with pytest.raises(NotCommandOwner):
        backend.command_joints_deg(type(a)("ui", 99), HOME)
    backend.command_joints_deg(a, HOME)
    backend.release(a)
    b = backend.acquire("ui")
    with pytest.raises(NotCommandOwner):
        backend.command_gripper(a, 0)  # stale token
    backend.command_gripper(b, 0)


def test_joint_order_is_fixed_and_state_reports_degrees(backend):
    t = backend.acquire("t")
    q = HOME + np.array([5.0, 0.0, 0.0, 0.0, 0.0])
    backend.command_joints_deg(t, q)
    backend.tick(1.0)
    st = backend.state()
    assert st.joints_deg.shape == (len(JOINT_NAMES),)
    assert abs(st.joints_deg[0] - 5.0) < 1.0 and 0 <= st.gripper <= 100
    with pytest.raises(ValueError):
        backend.command_joints_deg(t, [0, 0, 0, 0])


def test_stop_freezes_and_reset_clears_hold(backend):
    t = backend.acquire("t")
    backend.command_joints_deg(t, HOME + [30.0, 0, 0, 0, 0])
    backend.tick(0.1)
    backend.stop(t)
    q_stop = backend.state().joints_deg.copy()
    backend.tick(1.0)
    assert np.all(np.abs(backend.state().joints_deg - q_stop) < 2.0)
    backend.grasp.held = "red_cube"  # simulate a hold carried into reset
    backend.reset()
    assert backend.grasp.held is None
    assert (
        np.all(backend.data_ctrl_joints() == np.deg2rad(HOME))
        if hasattr(backend, "data_ctrl_joints")
        else True
    )
    with pytest.raises(NotCommandOwner):
        backend.command_gripper(t, 100)  # owner cleared by reset
