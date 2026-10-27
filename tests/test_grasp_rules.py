"""Simplified-grasp rules: hold only one object in the pocket, never snap, never carry over."""

import numpy as np

from so101_demo.execution.primitives import grasp_tcp_for
from so101_demo.kinematics import So101Kinematics, solve_top_down
from so101_demo.plan import load_registry
from so101_demo.robot import MujocoBackend
from so101_demo.sim.grasp import SimpleGrasp
from so101_demo.sim.scene import SceneConfig, load_scene, set_object_pose

REG = load_registry()


def _setup(objects):
    sim = load_scene(SceneConfig("sim-v1-simple", objects))
    kin = So101Kinematics(sim.arm_limits_rad())
    grasp = SimpleGrasp(sim, objects)
    home = solve_top_down(kin, (0.19, 0.0, 0.07))
    backend = MujocoBackend(sim, grasp, home_q_deg=home)
    return sim, kin, grasp, backend


def _put_tcp_at_cube(sim, kin, backend, token, centre):
    # Teleport the arm and command the same posture, so ticking holds it there.
    q = solve_top_down(kin, grasp_tcp_for(np.asarray(centre), 0.04, 0.0035))
    sim.set_arm_joints_deg(q)
    backend.command_joints_deg(token, q)
    return q


def test_close_far_from_any_object_holds_nothing():
    sim, kin, grasp, backend = _setup(("red_cube",))
    t = backend.acquire("t")
    backend.command_gripper(t, 0)
    assert grasp.held is None and grasp.events[-1].action == "no_hold"


def test_close_with_one_object_in_pocket_holds_it_without_moving_it():
    sim, kin, grasp, backend = _setup(("red_cube",))
    centre = (0.19, 0.0, 0.02)
    set_object_pose(sim, "red_cube", centre)
    t = backend.acquire("t")
    _put_tcp_at_cube(sim, kin, backend, t, centre)
    before = sim.body_pose("red_cube")[:3, 3].copy()
    backend.command_gripper(t, 30)
    assert grasp.held == "red_cube"
    after = sim.body_pose("red_cube")[:3, 3]
    assert np.linalg.norm(after - before) < 1e-6  # no snap at activation
    backend.tick(0.2)
    assert np.linalg.norm(sim.body_pose("red_cube")[:3, 3] - before) < 0.002


def test_two_objects_in_pocket_are_refused():
    sim, kin, grasp, backend = _setup(("red_cube", "blue_cube"))
    centre = (0.19, 0.0, 0.02)
    set_object_pose(sim, "red_cube", centre)
    set_object_pose(sim, "blue_cube", (0.19, 0.0, 0.06))  # stacked, both inside the zone
    t = backend.acquire("t")
    _put_tcp_at_cube(sim, kin, backend, t, (0.19, 0.0, 0.04))
    backend.command_gripper(t, 30)
    assert grasp.held is None and len(grasp.events[-1].candidates) == 2


def test_open_releases_and_reset_does_not_carry_over():
    sim, kin, grasp, backend = _setup(("red_cube",))
    centre = (0.19, 0.0, 0.02)
    set_object_pose(sim, "red_cube", centre)
    t = backend.acquire("t")
    _put_tcp_at_cube(sim, kin, backend, t, centre)
    backend.command_gripper(t, 30)
    assert grasp.held == "red_cube"
    backend.command_gripper(t, 100)
    assert grasp.held is None and not sim.data.eq_active.any()
    backend.command_gripper(t, 30)
    assert grasp.held == "red_cube"
    backend.reset()
    assert grasp.held is None and not sim.data.eq_active.any()


def test_hold_does_not_correct_an_unreached_target():
    # The cube is 30 mm outside the pocket: closing must not pull it in.
    sim, kin, grasp, backend = _setup(("red_cube",))
    set_object_pose(sim, "red_cube", (0.19, 0.0, 0.02))
    t = backend.acquire("t")
    _put_tcp_at_cube(sim, kin, backend, t, (0.19, 0.05, 0.02))
    before = sim.body_pose("red_cube")[:3, 3].copy()
    backend.command_gripper(t, 30)
    backend.tick(0.5)
    assert grasp.held is None
    assert np.linalg.norm(sim.body_pose("red_cube")[:3, 3] - before) < 0.002
