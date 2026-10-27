"""Issue #10: the venue styling is visual only, and the label projection is exact."""

import mujoco
import numpy as np

from so101_demo.evidence.overlay import LABEL_TEXT, project
from so101_demo.evidence.record import work_area_camera
from so101_demo.plan import load_registry
from so101_demo.sim import appearance as A
from so101_demo.sim.scene import SceneConfig, build_scene

PHYSICS_ARRAYS = (
    "body_mass",
    "body_inertia",
    "body_pos",
    "body_quat",
    "body_ipos",
    "body_iquat",
    "body_parentid",
    "geom_type",
    "geom_bodyid",
    "geom_size",
    "geom_pos",
    "geom_quat",
    "geom_friction",
    "geom_solref",
    "geom_solimp",
    "geom_margin",
    "geom_gap",
    "geom_condim",
    "geom_contype",
    "geom_conaffinity",
    "geom_priority",
    "jnt_type",
    "jnt_range",
    "jnt_stiffness",
    "jnt_qposadr",
    "dof_damping",
    "dof_armature",
    "dof_frictionloss",
    "eq_type",
    "eq_data",
    "eq_solref",
    "eq_solimp",
    "eq_active0",
    "actuator_gainprm",
    "actuator_biasprm",
    "actuator_ctrlrange",
    "actuator_forcerange",
    "site_pos",
)


def _pair():
    reg = load_registry()
    cfg = SceneConfig("sim-v1-simple", tuple(reg.objects))
    return build_scene(cfg, reg), build_scene(cfg, reg, styled=True)


def test_styling_adds_only_visual_elements():
    plain, styled = _pair()
    assert styled.vis.global_.offwidth == A.OFFSCREEN_WIDTH
    assert styled.vis.global_.offheight == A.OFFSCREEN_HEIGHT
    assert styled.nlight == plain.nlight + 1 and styled.ntex == plain.ntex + 1
    assert mujoco.mj_name2id(styled, mujoco.mjtObj.mjOBJ_LIGHT, A.KEY_LIGHT_NAME) >= 0
    assert styled.light_castshadow[-1]
    assert (plain.ngeom, plain.nbody, plain.njnt, plain.neq, plain.nu) == (
        styled.ngeom,
        styled.nbody,
        styled.njnt,
        styled.neq,
        styled.nu,
    )


def test_styling_leaves_every_physics_array_and_option_unchanged():
    plain, styled = _pair()
    for name in PHYSICS_ARRAYS:
        np.testing.assert_array_equal(getattr(plain, name), getattr(styled, name), err_msg=name)
    for name in dir(plain.opt):
        if name.startswith("_"):
            continue
        a, b = getattr(plain.opt, name), getattr(styled.opt, name)
        if callable(a):
            continue
        np.testing.assert_array_equal(np.asarray(a), np.asarray(b), err_msg=f"opt.{name}")


def test_styled_and_plain_scenes_follow_the_same_trajectory():
    plain, styled = _pair()
    dp, ds = mujoco.MjData(plain), mujoco.MjData(styled)
    n = int(2.0 / plain.opt.timestep)
    for i in range(n):
        ctrl = 0.3 * np.sin(0.002 * i + np.arange(plain.nu))
        dp.ctrl[:] = ctrl
        ds.ctrl[:] = ctrl
        mujoco.mj_step(plain, dp)
        mujoco.mj_step(styled, ds)
    np.testing.assert_array_equal(dp.qpos, ds.qpos)
    np.testing.assert_array_equal(dp.qvel, ds.qvel)


def test_projection_puts_the_lookat_point_at_the_image_centre():
    # mjv_updateScene fills the GL cameras without any GL context
    _, styled = _pair()
    data = mujoco.MjData(styled)
    mujoco.mj_forward(styled, data)
    scene = mujoco.MjvScene(styled, maxgeom=2000)
    cam = work_area_camera()
    mujoco.mjv_updateScene(
        styled, data, mujoco.MjvOption(), None, cam, mujoco.mjtCatBit.mjCAT_ALL, scene
    )
    u, v = project(scene, 1920, 1080, cam.lookat)
    assert abs(u - 960) < 0.5 and abs(v - 540) < 0.5
    # a point above the lookat goes up in the image, a point behind the camera is None
    _, v_up = project(scene, 1920, 1080, np.asarray(cam.lookat) + (0, 0, 0.1))
    assert v_up < v
    behind = np.asarray(scene.camera[0].pos) - 0.5 * np.asarray(scene.camera[0].forward)
    assert project(scene, 1920, 1080, behind) is None


def test_labels_name_every_registered_id_in_words_not_only_colour():
    reg = load_registry()
    for oid in list(reg.objects) + list(reg.destinations):
        assert LABEL_TEXT[oid].strip() and LABEL_TEXT[oid] != oid
    assert LABEL_TEXT["box_a"].endswith("A") and LABEL_TEXT["box_b"].endswith("B")
