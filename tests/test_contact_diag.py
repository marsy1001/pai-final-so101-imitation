"""Contact diagnosis helpers: force sign convention and the kinematic sweep (no physics change)."""

import mujoco
import numpy as np

from so101_demo.evidence.contact_diag import Classifier, contact_rows, kinematic_sweep
from so101_demo.sim.scene import SceneConfig, load_scene


def test_normal_force_on_cube_from_table_points_up():
    # Known contact: the resting cube on the table. The derived world-frame normal force
    # on the cube must point +z regardless of the geom order MuJoCo picked.
    sim = load_scene(SceneConfig("sim-v1-contact", ("red_cube",)))
    sim.step(500)
    cls = Classifier.build(sim.model, "red_cube")
    rows = [
        r
        for r in contact_rows(sim.model, sim.data, cls, 0, "rest", forces=True)
        if r["pair_class"] == "cube-table"
    ]
    assert rows, "expected cube-table contacts at rest"
    active = [r for r in rows if r["efc_address"] >= 0]
    assert active
    total_up = sum(r["fn_on_cube_world_z"] for r in active)
    assert total_up > 0.15  # ~ weight of a 20 g cube (0.196 N) carried by the table
    assert all(
        abs(r["fn_on_cube_world_x"]) < 0.05 and abs(r["fn_on_cube_world_y"]) < 0.05 for r in active
    )


def test_kinematic_sweep_finds_moving_jaw_first_contact_and_reports_pre_close_state():
    from so101_demo.execution.primitives import grasp_tcp_for
    from so101_demo.kinematics import So101Kinematics, solve_top_down

    sim = load_scene(SceneConfig("sim-v1-contact", ("red_cube",)))
    kin = So101Kinematics(sim.arm_limits_rad())
    centre = np.array([0.19, 0.0, 0.02])
    q = solve_top_down(kin, grasp_tcp_for(centre, 0.04, 0.0))
    sw = kinematic_sweep(q, centre, coarse_step_deg=1.0, fine_step_deg=0.25)
    mv = sw.first_contact["cube-moving_jaw"]
    assert (
        15.0 < mv["angle_deg"] < 35.0
    )  # a 40 mm cube: pads ~38 mm apart at 20 deg, 47 mm at 30 deg
    assert sw.gap_at_first_contact_mm is not None and abs(sw.gap_at_first_contact_mm - 40.0) < 1.0
    # sweep only moves the jaw: the arm posture and the cube are exactly where we put them
    assert np.allclose(sw.arm_q_deg, q) and np.allclose(sw.cube_start, centre)
    assert isinstance(sw.pre_close_interference, list)
    assert mujoco.mj_name2id(sim.model, mujoco.mjtObj.mjOBJ_GEOM, "red_cube_geom") >= 0
