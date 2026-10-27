"""Headless reference run of the fixed 0b manifest under the simplified grasp, plus rejections."""

import numpy as np
import pytest

from so101_demo.evidence.place_margin import MARGIN_MIN_M, measure
from so101_demo.execution import PickPlace, SafetyLimits, SafetyRejected, criteria
from so101_demo.execution.primitives import fixed_held_offset_m, held_offset_is_plausible
from so101_demo.execution.trial import run_trial
from so101_demo.kinematics import So101Kinematics, grasp_yaw_error_deg, solve_top_down
from so101_demo.locate import SimGroundTruth
from so101_demo.plan import load_registry
from so101_demo.robot import MujocoBackend
from so101_demo.sim.grasp import SimpleGrasp
from so101_demo.sim.scene import SceneConfig, load_scene, set_object_pose

REG = load_registry()

# Trials expected to fail, by class. Empty since the placing-margin fix of 2026-09-20:
# before it, with the trays at (0.22, +-0.09), T2 (blue from (0.22, 0) to box_b) landed on
# the +x wall tilted 12.7 deg (measured 2026-09-20 on the old project). An entry here
# asserts that class explicitly, so any other outcome fails the test and forces a change.
EXPECTED_FAILURE: dict[str, str] = {}


@pytest.mark.parametrize("trial", REG.trials_0b, ids=[t.trial for t in REG.trials_0b])
def test_reference_trial_outcome_under_simplified_grasp(trial):
    r = run_trial(trial, "sim-v1-simple")
    expected = EXPECTED_FAILURE.get(trial.trial)
    if expected is None:
        assert r.verdict.success, (r.verdict.failure, r.verdict.details)
        # Placing-margin acceptance on the same run: at least MARGIN_MIN_M between every
        # bottom corner and the nearest inner wall, centred, edges parallel to the tray (the
        # metrics of docs/place-margin.md), using the measured grasp offset,
        # which lies along the jaw axis.
        m = measure(r, REG)
        assert m["margin_mm"] >= MARGIN_MIN_M * 1000, m
        assert abs(m["yaw_vs_tray_deg"]) <= 3.0, m
        assert m["centre_offset_norm_mm"] <= 3.0, m
        x, y, z = m["held_offset_mm"]
        assert abs(x) < 1.0 and abs(y) < 1.0 and 25.0 < z < 35.0, m
    else:
        assert not r.verdict.success and r.verdict.failure == expected, (
            r.verdict.failure,
            r.verdict.details,
        )
        # the known shape: released over the tray, resting on a wall (tilted, not inside)
        assert r.verdict.details["inside"] is False
        assert r.verdict.final_tilt_deg > 5.0
    assert r.initial_penetrations == []
    assert r.sim_time_s < 60.0


@pytest.mark.parametrize("trial", REG.trials_0b, ids=[t.trial for t in REG.trials_0b])
def test_contact_baseline_runs_and_is_classified(trial):
    # The contact configuration is recorded, not required to pass on the laptop:
    # every trial must end in a verdict with the numeric diagnostics filled in and
    # with no weld anywhere in the model (nothing but contact can carry the cube).
    r = run_trial(trial, "sim-v1-contact")
    assert r.verdict.success or r.verdict.failure in criteria.FAILURE_CLASSES
    # a missed grasp must still be classified from the samples, never turned into a
    # `rejected` place target by a nonsense held-offset reading (Issue #21 review)
    assert r.verdict.failure != "rejected", r.verdict.details
    assert r.diagnostics["welds_in_model"] == 0
    assert "contacts_at_close" in r.diagnostics and "max_penetration_at_close_mm" in r.diagnostics
    assert r.initial_penetrations == []


def test_unknown_config_is_refused():
    with pytest.raises(ValueError):
        run_trial(REG.trials_0b[0], "sim-v2-magic")


def _pp(objects=("red_cube",)):
    sim = load_scene(SceneConfig("sim-v1-simple", objects))
    kin = So101Kinematics(sim.arm_limits_rad())
    backend = MujocoBackend(
        sim, SimpleGrasp(sim, objects), home_q_deg=solve_top_down(kin, (0.19, 0.0, 0.07))
    )
    token = backend.acquire("t")
    return sim, PickPlace(
        backend, kin, SimGroundTruth(sim, objects), REG, SafetyLimits(sim.arm_limits_rad()), token
    )


def test_unregistered_ids_are_refused_before_moving():
    sim, pp = _pp()
    with pytest.raises(SafetyRejected) as e:
        pp.pick("green_ball")
    assert e.value.reason == "unregistered_object"
    with pytest.raises(SafetyRejected):
        pp.place("box_c", "red_cube")
    assert pp.trace == [] or all(not label.endswith("pre_grasp") for _, label in pp.trace)


def test_object_outside_workspace_is_refused_without_motion():
    sim, pp = _pp()
    set_object_pose(sim, "red_cube", (0.40, 0.0, 0.02))
    q0 = pp.backend.state().joints_deg.copy()
    with pytest.raises(SafetyRejected) as e:
        pp.pick("red_cube")
    assert e.value.reason == "outside_workspace"
    assert np.allclose(pp.backend.state().joints_deg, q0)


def test_stop_refuses_primitives_and_sends_nothing():
    sim, pp = _pp()
    pp.stop()
    n_cmd = len(pp.backend.command_log)
    held = pp.backend.grasp.held
    trace_before = list(pp.trace)
    with pytest.raises(SafetyRejected) as e:
        pp.pick("red_cube")
    assert e.value.reason == "stopped"
    with pytest.raises(SafetyRejected):
        pp.place("box_a", "red_cube")
    assert len(pp.backend.command_log) == n_cmd  # no joint or gripper command after stop
    assert pp.backend.grasp.held == held
    assert pp.trace == trace_before  # no milestone recorded after stop


def test_stop_mid_motion_aborts_before_the_next_command():
    from so101_demo.execution import primitives as P

    sim, pp = _pp()
    calls = {"n": 0}
    orig = pp.backend.tick

    def tick(dt):
        orig(dt)
        calls["n"] += 1
        if calls["n"] == 5:
            pp.stop()

    pp.backend.tick = tick
    with pytest.raises(SafetyRejected) as e:
        pp.pick("red_cube")
    assert e.value.reason == "stopped"
    after_stop = [
        c
        for c in pp.backend.command_log
        if c[0] > next(t for t, label in pp.trace if label == "stop")
    ]
    assert after_stop == []
    assert not any(
        label.startswith("pick:") and label != "pick:red_cube:start" for _, label in pp.trace
    )
    assert P.CONTROL_DT_S > 0


def test_trial_stops_at_the_sim_time_deadline(monkeypatch):
    from so101_demo.execution import criteria as C

    monkeypatch.setattr(C, "TIMEOUT_S", 2.0)
    r = run_trial(REG.trials_0b[0], "sim-v1-simple")
    assert r.verdict.failure == "timeout"
    assert r.sim_time_s <= 2.0 + 0.05  # one control tick of slack


def test_pick_and_place_use_object_and_tray_yaw():
    # The jaws open along the cube's registered yaw at the descent and along the tray's
    # axes at the release (Issue #21); the tip pose is read from the sim at both marks.
    sim, pp = _pp()
    seen = {}
    orig = pp._mark

    def mark(label):
        orig(label)
        if label in ("pick:descended", "place:descended"):
            seen[label] = sim.tip_pose()

    pp._mark = mark
    pp.pick("red_cube")
    pp.place("box_a", "red_cube")
    assert grasp_yaw_error_deg(seen["pick:descended"], REG.objects["red_cube"].yaw_deg) < 2.0
    assert grasp_yaw_error_deg(seen["place:descended"], REG.destinations["box_a"].yaw_deg) < 2.0
    assert pp.held_offset_m is not None and 0.025 < pp.held_offset_m[2] < 0.035


def test_place_without_measurement_uses_the_fixed_held_offset():
    # The real-robot path (docs/place-margin.md): no observation of the held cube, so the
    # fixed value (design pocket position + measured push) stands in, per grasp config.
    sim, pp = _pp()
    pp.measure_held_offset = False
    pp.pick("red_cube")
    assert pp.held_offset_source == "fixed"
    np.testing.assert_allclose(pp.held_offset_m, fixed_held_offset_m(0.04, 0.0035))
    assert pp.held_offset_m[2] == pytest.approx(0.0296, abs=1e-4)
    pp.place("box_a", "red_cube")
    assert [label for _, label in pp.trace][-1] == "place:retreated"


def test_missed_grasp_falls_back_to_the_design_offset():
    # Cube moved away after the pre-grasp: the close misses, the "held offset" measured
    # after the lift is the cube on the table, far outside the grasp zone. It must not be
    # used (it would send the place target out of the work area, Issue #21 review).
    sim, pp = _pp()
    orig = pp._mark

    def mark(label):
        orig(label)
        if label == "pick:pre_grasp":
            set_object_pose(sim, "red_cube", (0.16, -0.03, 0.02))

    pp._mark = mark
    pp.pick("red_cube")
    assert pp.held_offset_source == "design"
    np.testing.assert_allclose(pp.held_offset_m, [0.0, 0.0, 0.0235])
    pp.place("box_a", "red_cube")  # runs to the end; the judge classifies it as no_lift
    assert [label for _, label in pp.trace][-1] == "place:retreated"


def test_held_offset_plausibility_is_the_grasp_zone():
    assert held_offset_is_plausible([0.0, 0.0, 0.0296])
    assert held_offset_is_plausible([0.0, 0.0, 0.0235])
    assert not held_offset_is_plausible([0.05, 0.0, 0.03])  # cube left on the table
    assert not held_offset_is_plausible([0.0, 0.0, 0.05])
    assert not held_offset_is_plausible([0.0, np.nan, 0.03])
