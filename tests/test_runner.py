"""Stage 1 step 4a (execution side): fixtures, approved entry, transitions, reset, rejections."""

import dataclasses
import json

import pytest

from so101_demo.execution import criteria as C
from so101_demo.execution.runner import (
    ApprovedPlan,
    PlanRunner,
    ResetCheck,
    RunnerState,
    approve_for_development,
    approve_from_review,
    parse_plan,
)
from so101_demo.kinematics.top_down import WORKSPACE_MAX, WORKSPACE_MIN
from so101_demo.plan import PlanInvalid, load_registry, validate_plan
from so101_demo.plan.paths import FIXTURES_4A_DIR

REG = load_registry()
FIXTURES = sorted(FIXTURES_4A_DIR.glob("*.json"))
STATES = [RunnerState.RUNNING, RunnerState.DONE, RunnerState.RESETTING, RunnerState.IDLE]


def _approved(path):
    return approve_for_development(
        parse_plan(path.read_text(encoding="utf-8")), path.stem, approve_fixed_fixture=True
    )


@pytest.fixture(scope="module")
def runner() -> PlanRunner:
    return PlanRunner("sim-v1-simple")


# --- fixtures ----------------------------------------------------------------------
def test_four_fixtures_cover_every_combination():
    assert len(FIXTURES) == 4
    plans = [validate_plan(json.loads(p.read_text(encoding="utf-8"))) for p in FIXTURES]
    assert all(p.executable for p in plans)
    combos = {(p.target, p.destination) for p in plans}
    assert combos == {(o, d) for o in REG.objects for d in REG.destinations}
    for p in plans:
        assert p.target in REG.objects and p.destination in REG.destinations
        assert [s.action for s in p.steps] == ["pick", "place"]


def test_home_positions_are_inside_the_work_area_and_apart():
    homes = {k: v.home_xyz_m for k, v in REG.objects.items()}
    for xyz in homes.values():
        assert WORKSPACE_MIN[0] <= xyz[0] <= WORKSPACE_MAX[0]
        assert WORKSPACE_MIN[1] <= xyz[1] <= WORKSPACE_MAX[1]
    (a, b) = homes.values()
    assert abs(a[0] - b[0]) >= 0.06 or abs(a[1] - b[1]) >= 0.04  # never edge to edge


# --- the four combinations, object switched every time, reset between ----------------
def test_runner_starts_idle_with_a_passing_reset(runner):
    assert runner.state is RunnerState.IDLE
    assert runner.last_reset.passed, runner.last_reset.reasons


@pytest.mark.parametrize("path", FIXTURES, ids=[p.stem for p in FIXTURES])
def test_fixture_runs_to_success_and_resets(runner, path):
    rec = runner.submit(_approved(path))
    assert rec.outcome == "success", (rec.outcome, rec.reason, rec.verdict and rec.verdict.details)
    assert rec.approval_source == "dev-fixed-fixture"
    assert [t.state for t in rec.transitions] == STATES
    assert rec.reset_previous.passed and rec.reset_after.passed, rec.reset_after.reasons
    assert set(rec.scene_at_submit) == set(REG.objects)
    # the other cube is not touched while this one is moved
    assert max(rec.other_objects_max_displacement_m.values()) < C.RESET_OBJECT_POS_TOL_M
    assert rec.commands_sent > 0
    assert runner.state is RunnerState.IDLE
    # judged from the object pose, never from the hold list
    assert rec.verdict.success and rec.verdict.details["release_samples_audited"] > 0


# --- no approval bypass ------------------------------------------------------------
def test_development_approval_needs_the_explicit_flag():
    plan = parse_plan(FIXTURES[0].read_text(encoding="utf-8"))
    with pytest.raises(PermissionError):
        approve_for_development(plan, "F1", approve_fixed_fixture=False)
    ok = approve_for_development(plan, "F1", approve_fixed_fixture=True)
    assert isinstance(ok, ApprovedPlan) and ok.source == "dev-fixed-fixture"
    rv = approve_from_review(plan, "run-0001")
    assert rv.source == "approval-screen" and rv.reference == "run-0001"


def test_non_ok_plans_cannot_be_approved():
    plan = validate_plan(
        {"status": "unsupported", "target": None, "destination": None, "steps": [], "note": "x"}
    )
    with pytest.raises(ValueError):
        approve_for_development(plan, "x", approve_fixed_fixture=True)
    with pytest.raises(ValueError):
        approve_from_review(plan, "run")


@pytest.mark.parametrize(
    "submission",
    [
        pytest.param(lambda: parse_plan(FIXTURES[0].read_text(encoding="utf-8")), id="Plan"),
        pytest.param(lambda: FIXTURES[0].read_text(encoding="utf-8"), id="json-text"),
        pytest.param(lambda: json.loads(FIXTURES[0].read_text(encoding="utf-8")), id="dict"),
    ],
)
def test_unapproved_submissions_are_rejected_without_a_command(runner, submission):
    rec = runner.submit(submission())
    assert rec.outcome == "rejected" and rec.reason.startswith("unapproved")
    assert rec.commands_sent == 0
    assert [t.state for t in rec.transitions] == STATES[1:]  # DONE(rejected) -> RESETTING -> IDLE
    assert rec.reset_after.passed


# --- the four rejection tests --------------------------------------------------------
def test_rejects_unregistered_object(runner):
    text = json.dumps(
        {
            "status": "ok",
            "target": "green_ball",
            "destination": "box_a",
            "steps": [
                {"action": "pick", "object": "green_ball"},
                {"action": "place", "destination": "box_a"},
            ],
            "note": "x",
        }
    )
    rec = runner.submit(text)
    assert rec.outcome == "rejected" and rec.reason.startswith("invalid_plan")
    assert rec.commands_sent == 0 and runner.state is RunnerState.IDLE


def test_rejects_target_outside_the_work_area_without_motion(runner):
    runner.place_object("red_cube", (0.40, 0.02, 0.02))
    rec = runner.submit(_approved(FIXTURES[0]))
    assert rec.outcome == "rejected" and "outside_workspace" in rec.reason
    assert rec.commands_sent == 0
    assert rec.scene_at_submit["red_cube"][0] == pytest.approx(0.40, abs=1e-6)
    assert [t.state for t in rec.transitions] == STATES  # refused inside RUNNING, then reset
    assert rec.reset_after.passed  # the cube is back home after the reset


def test_rejects_undecodable_bytes(runner):
    rec = runner.submit(b"\xff\xfe{")
    assert rec.outcome == "rejected" and rec.reason.startswith("invalid_plan")
    assert rec.commands_sent == 0


def test_unexpected_exception_in_a_hook_ends_as_error_and_resets(runner):
    def boom(r: PlanRunner) -> None:
        if r._pp is not None and r._pp.trace and r._pp.trace[-1][1] == "pick:pre_grasp":
            r.place_object("green_ball", (0.1, 0.1, 0.02))  # ValueError: not in the scene

    runner.on_tick = boom
    try:
        rec = runner.submit(_approved(FIXTURES[0]))
    finally:
        runner.on_tick = None
    assert rec.outcome == "error" and rec.reason.startswith("ValueError")
    assert [t.state for t in rec.transitions] == STATES
    assert rec.reset_after.passed and runner.state is RunnerState.IDLE
    # the runner is usable again
    assert runner.submit(_approved(FIXTURES[1])).outcome == "success"


@pytest.mark.parametrize(
    "text",
    [
        pytest.param('{"status": "ok", "target": "red_cube",', id="syntax"),
        pytest.param(
            json.dumps(
                {
                    "status": "ok",
                    "target": "red_cube",
                    "destination": "box_a",
                    "steps": [
                        {"action": "pick", "object": "red_cube"},
                        {"action": "place", "destination": "box_a"},
                    ],
                }
            ),
            id="missing-note",
        ),
        pytest.param(
            json.dumps(
                {
                    "status": "ok",
                    "target": "red_cube",
                    "destination": "box_a",
                    "steps": [],
                    "note": "x",
                }
            ),
            id="ok-without-steps",
        ),
        pytest.param(
            json.dumps(
                {
                    "status": "ok",
                    "target": "red_cube",
                    "destination": "box_a",
                    "steps": [
                        {"action": "pick", "object": "blue_cube"},
                        {"action": "place", "destination": "box_a"},
                    ],
                    "note": "x",
                }
            ),
            id="steps-disagree",
        ),
    ],
)
def test_rejects_invalid_json(runner, text):
    with pytest.raises(PlanInvalid):
        parse_plan(text)
    rec = runner.submit(text)
    assert rec.outcome == "rejected" and rec.reason.startswith("invalid_plan")
    assert rec.commands_sent == 0


def test_close_far_from_the_object_holds_nothing_and_moves_nothing(runner):
    def displace(r: PlanRunner) -> None:
        if r._pp is not None and r._pp.trace and r._pp.trace[-1][1] == "pick:pre_grasp":
            r.place_object("red_cube", (0.16, -0.03, 0.02))

    runner.on_tick = displace
    try:
        rec = runner.submit(_approved(FIXTURES[0]))
    finally:
        runner.on_tick = None
    assert rec.outcome == "no_lift"
    closes = [e for e in rec.grasp_events if e["action"] != "release"]
    assert closes and all(e["action"] == "no_hold" for e in closes)
    # the displaced cube stayed on the table where it was put: never lifted, never snapped
    assert rec.verdict.max_lift_z < C.LIFT_CENTER_Z_M
    assert rec.reset_after.passed


# --- stop and busy -----------------------------------------------------------------
def test_stop_mid_run_ends_as_stopped_and_resets(runner):
    def stop_after_close(r: PlanRunner) -> None:
        if r._pp is not None and r._pp.trace and r._pp.trace[-1][1] == "pick:closed":
            r.stop()

    runner.on_tick = stop_after_close
    try:
        rec = runner.submit(_approved(FIXTURES[1]))
    finally:
        runner.on_tick = None
    assert rec.outcome == "stopped"
    assert rec.trace[-1][1] == "stop" and "place:released" not in [label for _, label in rec.trace]
    assert [t.state for t in rec.transitions] == STATES
    assert rec.reset_after.passed and rec.reset_after.held is None
    assert runner.state is RunnerState.IDLE


def test_submit_while_running_is_rejected_as_busy(runner):
    inner = {}

    def resubmit(r: PlanRunner) -> None:
        if "rec" not in inner and r.state is RunnerState.RUNNING:
            inner["rec"] = r.submit(_approved(FIXTURES[0]))

    runner.on_tick = resubmit
    try:
        outer = runner.submit(_approved(FIXTURES[0]))
    finally:
        runner.on_tick = None
    assert inner["rec"].outcome == "busy" and inner["rec"].transitions == []
    assert outer.outcome == "success"
    assert runner.state is RunnerState.IDLE


def test_reset_check_values_are_the_fixed_ones():
    assert C.RESET_OBJECT_POS_TOL_M == 0.001
    assert C.RESET_OBJECT_TILT_MAX_DEG == 1.0
    assert C.RESET_JOINT_TOL_DEG == 1.0
    assert C.GRIPPER_OPEN_MIN == 60.0 and C.RESET_SETTLE_S == 1.0


# --- review round 1 (Codex, 2026-09-19) ---------------------------------------------
def _tampered_plan():
    p = parse_plan(FIXTURES[0].read_text(encoding="utf-8"))  # target red_cube
    return dataclasses.replace(
        p, steps=(dataclasses.replace(p.steps[0], object="blue_cube"), p.steps[1])
    )


def test_approval_revalidates_a_hand_built_plan():
    with pytest.raises(PlanInvalid):
        approve_for_development(_tampered_plan(), "F1", approve_fixed_fixture=True)
    with pytest.raises(PlanInvalid):
        approve_from_review(_tampered_plan(), "run")


def test_runner_revalidates_an_approved_plan_before_any_command(runner):
    good = _approved(FIXTURES[0])
    # bypass __post_init__ the way a careless caller could: mutate the frozen field
    object.__setattr__(good, "plan", _tampered_plan())
    rec = runner.submit(good)
    assert rec.outcome == "rejected" and rec.reason.startswith("invalid_plan")
    assert rec.commands_sent == 0 and runner.state is RunnerState.IDLE


def test_persistently_failing_observer_never_blocks_the_runner():
    calls = {"n": 0}

    def observer(sim):
        def cb(s):
            calls["n"] += 1
            raise OSError("recorder failed persistently")

        return cb

    r = PlanRunner("sim-v1-simple", observer=observer)  # the initial reset must not raise
    assert r.state is RunnerState.IDLE and r.last_reset.passed
    rec = r.submit(_approved(FIXTURES[0]))
    assert rec.outcome == "success"  # physics and judgement are unaffected
    assert rec.observer_error.startswith("OSError")
    assert calls["n"] == 1  # muted after the first failure in the run
    assert r.state is RunnerState.IDLE and rec.reset_after.passed
    rec2 = r.submit(_approved(FIXTURES[1]))
    assert rec2.outcome == "success" and rec2.observer_error.startswith("OSError")


def test_reset_failure_is_not_a_pass(runner, monkeypatch):
    real = runner.check_reset

    def failing(pen=None):
        c = real(pen)
        return ResetCheck(
            False,
            c.object_pos_err_m,
            c.object_tilt_deg,
            c.joint_err_deg,
            c.gripper,
            c.held,
            c.constraints_active,
            c.penetrations,
            ["blue_cube 20.00 mm from home (injected)"],
        )

    monkeypatch.setattr(runner, "check_reset", failing)
    rec = runner.submit(_approved(FIXTURES[0]))
    monkeypatch.undo()
    assert rec.verdict.success  # pick/place itself was fine
    assert rec.outcome == "reset_failed" and "injected" in rec.reason
    assert not rec.reset_after.passed
    # the next submission re-resets first and runs normally
    rec2 = runner.submit(_approved(FIXTURES[1]))
    assert rec2.outcome == "success" and rec2.reset_previous.passed
