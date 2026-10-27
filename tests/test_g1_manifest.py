"""G1 preparation (Issue #19): the fixed 10-trial manifest, its rules, and the
evidence runner's staging and record fields. The
manifest rules are the XPS acceptance criteria (a)-(e) of the Issue; `check_manifest` is
the single implementation and is exercised both on the real file and on deliberately
broken copies. Since ADR 0008 (2026-09-21) both objects have a start position per trial,
so the rules apply to both and no object has to stay at its home."""

import dataclasses
import json
from types import SimpleNamespace

import numpy as np
import pytest

from so101_demo.evidence import stage1_g1
from so101_demo.execution import criteria as C
from so101_demo.execution.runner import PlanRunner, RunnerState
from so101_demo.kinematics.top_down import WORKSPACE_MAX, WORKSPACE_MIN
from so101_demo.plan import (
    G1Manifest,
    check_manifest,
    load_g1_manifest,
    load_registry,
    validate_plan,
)
from so101_demo.plan.manifest import (
    MIN_CENTRE_DISTANCE_M,
    MIN_REPEATS_PER_COMBINATION,
    TRIAL_COUNT,
    G1Trial,
    start_violations,
)
from so101_demo.plan.paths import FIXTURES_4A_DIR, G1_MANIFEST_PATH

REG = load_registry()
M = load_g1_manifest()

# The ten start patterns of ADR 0008 (the user picked them from the rendered layout on
# 2026-09-20): trial -> target, destination, red start, blue start. The manifest copies
# this table; the test fails if either drifts from the ADR.
ADR0008 = {
    "T01": ("red_cube", "box_a", (0.16, -0.08), (0.22, 0.02)),
    "T02": ("blue_cube", "box_b", (0.16, 0.06), (0.16, -0.04)),
    "T03": ("red_cube", "box_b", (0.20, 0.02), (0.16, 0.08)),
    "T04": ("blue_cube", "box_a", (0.16, -0.02), (0.23, 0.03)),
    "T05": ("red_cube", "box_a", (0.16, 0.04), (0.22, -0.03)),
    "T06": ("blue_cube", "box_b", (0.22, 0.03), (0.16, -0.08)),
    "T07": ("red_cube", "box_b", (0.16, -0.01), (0.16, 0.08)),
    "T08": ("blue_cube", "box_a", (0.16, -0.06), (0.20, 0.00)),
    "T09": ("red_cube", "box_a", (0.18, -0.03), (0.16, 0.06)),
    "T10": ("blue_cube", "box_b", (0.16, 0.08), (0.21, -0.02)),
}


def _with_trials(trials) -> G1Manifest:
    return dataclasses.replace(M, trials=tuple(trials))


# --- the real manifest ---------------------------------------------------------------
def test_manifest_file_is_the_frozen_path_and_valid():
    assert M.path == G1_MANIFEST_PATH and G1_MANIFEST_PATH.is_file()
    assert M.config_id == "sim-v1-simple"
    assert check_manifest(M, REG) == []


def test_ten_trials_with_unique_ids():
    assert len(M.trials) == TRIAL_COUNT == 10
    ids = [t.trial for t in M.trials]
    assert len(set(ids)) == 10
    assert ids == [f"T{i:02d}" for i in range(1, 11)]


def test_every_plan_validates_and_matches_its_trial():  # (a)
    for t in M.trials:
        assert t.fixture_path.parent == FIXTURES_4A_DIR
        plan = validate_plan(json.loads(t.fixture_path.read_text(encoding="utf-8")))
        assert plan.executable
        assert (plan.target, plan.destination) == (t.target, t.destination)
        assert t.target in REG.objects and t.destination in REG.destinations
        assert [s.action for s in plan.steps] == ["pick", "place"]


def test_every_combination_at_least_twice():  # (b)
    combos = {(o, d): 0 for o in REG.objects for d in REG.destinations}
    for t in M.trials:
        combos[(t.target, t.destination)] += 1
    assert len(combos) == 4
    assert all(n >= MIN_REPEATS_PER_COMBINATION for n in combos.values()), combos


def test_target_switches_every_trial():  # (c)
    targets = [t.target for t in M.trials]
    assert all(a != b for a, b in zip(targets, targets[1:], strict=False))


def test_start_positions_inside_the_work_area_and_apart():  # (d)
    for t in M.trials:
        assert set(t.start_xy_m) == set(REG.objects)
        for oid, (x, y) in t.start_xy_m.items():
            assert WORKSPACE_MIN[0] <= x <= WORKSPACE_MAX[0], (t.trial, oid)
            assert WORKSPACE_MIN[1] <= y <= WORKSPACE_MAX[1], (t.trial, oid)
        (a, b) = (np.asarray(v) for v in t.start_xy_m.values())
        assert np.linalg.norm(a - b) >= MIN_CENTRE_DISTANCE_M - 1e-9, t.trial


def test_start_patterns_are_the_adr_0008_table_and_break_no_rule():
    assert {t.trial for t in M.trials} == set(ADR0008)
    for t in M.trials:
        target, destination, red, blue = ADR0008[t.trial]
        assert (t.target, t.destination) == (target, destination), t.trial
        assert t.start_xy_m == {"red_cube": red, "blue_cube": blue}, t.trial
        for oid, xy in t.start_xy_m.items():
            assert start_violations(REG, oid, xy, t.start_xy_m) == [], (t.trial, oid)
    # both objects move over the ten trials: neither is pinned to its home (ADR 0008)
    for oid in REG.objects:
        starts = {t.start_xy_m[oid] for t in M.trials}
        assert len(starts) >= 5, oid
    assert {t.fixture for t in M.trials} == {
        "F1-red_cube-box_a",
        "F2-blue_cube-box_b",
        "F3-red_cube-box_b",
        "F4-blue_cube-box_a",
    }


# --- the rules catch broken manifests ------------------------------------------------
def test_rules_reject_a_duplicate_id_and_a_wrong_count():  # (e), count
    dup = list(M.trials)
    dup[1] = dataclasses.replace(dup[1], trial="T01")
    problems = check_manifest(_with_trials(dup), REG)
    assert any("duplicate trial ids" in p for p in problems)
    short = check_manifest(_with_trials(M.trials[:9]), REG)
    assert any("9 trials, expected 10" in p for p in short)


def test_rules_reject_a_plan_that_does_not_match_the_trial():  # (a)
    bad = list(M.trials)
    bad[0] = dataclasses.replace(bad[0], destination="box_b")  # fixture F1 goes to box_a
    problems = check_manifest(_with_trials(bad), REG)
    assert any(
        "F1-red_cube-box_a is red_cube -> box_a, trial says red_cube -> box_b" in p
        for p in problems
    )
    missing = list(M.trials)
    missing[0] = dataclasses.replace(missing[0], fixture="F9-nope")
    assert any("missing" in p for p in check_manifest(_with_trials(missing), REG))


def test_rules_reject_a_missing_combination_and_a_repeated_target():  # (b), (c)
    same = list(M.trials)
    # T02 and T06 become copies of T01: blue -> box_b drops to 1 (T10), T01/T02 repeat red
    same[1] = dataclasses.replace(same[0], trial="T02")
    same[5] = dataclasses.replace(same[0], trial="T06")
    problems = check_manifest(_with_trials(same), REG)
    assert any("T01 and T02 both target red_cube" in p for p in problems)
    assert any("blue_cube -> box_b appears 1 time(s)" in p for p in problems)


def test_rules_reject_starts_outside_the_work_area_or_too_close():  # (d)
    far = list(M.trials)
    far[4] = dataclasses.replace(
        far[4], start_xy_m={**far[4].start_xy_m, "red_cube": (0.12, -0.03)}
    )
    assert any("outside the work area" in p for p in check_manifest(_with_trials(far), REG))
    close = list(M.trials)
    close[4] = dataclasses.replace(  # 30 mm from the blue start of T05 (0.22, -0.03)
        close[4], start_xy_m={**close[4].start_xy_m, "red_cube": (0.22, 0.00)}
    )
    problems = check_manifest(_with_trials(close), REG)
    assert any("30.0 mm apart, minimum 60 mm" in p for p in problems)
    # the rules cover the object that is not the target of the trial as well
    other = list(M.trials)
    other[4] = dataclasses.replace(
        other[4], start_xy_m={**other[4].start_xy_m, "blue_cube": (0.22, 0.07)}
    )
    assert any(
        "blue_cube start (0.22, 0.07) overlaps box_a" in p
        for p in check_manifest(_with_trials(other), REG)
    )


# --- staging and the evidence runner -------------------------------------------------
@pytest.fixture(scope="module")
def runner() -> PlanRunner:
    return PlanRunner(M.config_id)


def test_stage_puts_objects_at_the_start_and_passes_the_check(runner):
    t = M.trials[0]  # T01: both cubes away from their homes
    starts = {o: M.start_xyz(t, o, REG) for o in REG.objects}
    check = runner.stage(starts)
    assert check.passed, check.reasons
    assert runner.state is RunnerState.IDLE
    for o in REG.objects:
        assert (
            np.linalg.norm(runner.gt.pose_of(o).position_m - starts[o]) <= C.RESET_OBJECT_POS_TOL_M
        )
    # back to home by the runner's own reset, so the next submit starts clean
    assert runner.reset().passed


def test_stage_reports_an_object_that_cannot_rest_at_the_start(runner):
    t = M.trials[0]
    starts = {o: M.start_xyz(t, o, REG) for o in REG.objects}
    starts["red_cube"] = starts["red_cube"] + [0.0, 0.0, 0.03]  # dropped from 30 mm: it falls
    check = runner.stage(starts)
    assert not check.passed and any("red_cube" in r and "from start" in r for r in check.reasons)
    assert runner.reset().passed


@pytest.mark.parametrize("index", [0, 5])  # T01 (red -> A) and T06 (blue -> B)
def test_run_trial_records_start_check_and_run(runner, index):
    t = M.trials[index]
    start_check, rec = stage1_g1.run_trial(runner, M, t, True)
    assert start_check.passed, start_check.reasons
    assert rec.fixture_id == t.fixture and rec.approval_source == "dev-fixed-fixture"
    assert rec.outcome == "success", (rec.outcome, rec.reason)
    assert rec.reset_previous.passed and rec.reset_after.passed
    # the target really started where the manifest says
    assert np.allclose(
        rec.scene_at_submit[t.target][:2], t.start_xy_m[t.target], atol=C.RESET_OBJECT_POS_TOL_M
    )
    tr = stage1_g1.TrialRecord(t, start_check, rec, "test", "参考")
    assert tr.success and tr.result == "success"
    d = tr.as_dict()
    for key in (
        "trial",
        "fixture_id",
        "target",
        "destination",
        "start_xy_m",
        "start_check",
        "result",
        "outcome",
        "verdict",
        "commands_sent",
        "transitions",
        "reset_previous",
        "reset_after",
        "other_objects_max_displacement_mm",
        "sim_time_s",
        "wall_time_s",
        "machine",
        "label",
    ):
        assert key in d, key
    assert d["verdict"]["max_lift_z_m"] >= C.LIFT_CENTER_Z_M
    assert [x["state"] for x in d["transitions"]] == ["実行中", "完了", "リセット", "待機"]
    json.dumps(d)  # serialisable as written to trials.json


def test_trial_record_does_not_count_a_failed_start_or_reset_as_success():
    run = SimpleNamespace(outcome="success", reset_after=SimpleNamespace(passed=True))
    tr = stage1_g1.TrialRecord(M.trials[0], SimpleNamespace(passed=False), run, "x", "参考")
    assert not tr.success and tr.result == "start_check_failed"
    tr.start_check.passed = True
    run.reset_after.passed = False
    assert not tr.success
    run.reset_after.passed = True
    assert tr.success and tr.result == "success"
    run.outcome = "not_seated"
    assert not tr.success and tr.result == "not_seated"


def test_cli_refuses_without_the_approval_flag(capsys):
    assert stage1_g1.main([]) == 2
    assert "approve-fixed-fixture" in capsys.readouterr().err


def test_cli_refuses_a_manifest_that_breaks_the_rules(tmp_path, capsys):
    raw = json.loads(G1_MANIFEST_PATH.read_text(encoding="utf-8"))
    raw["trials"] = raw["trials"][:9]
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(raw), encoding="utf-8")
    assert (
        stage1_g1.main(["--approve-fixed-fixture", "--manifest", str(p), "--out", str(tmp_path)])
        == 2
    )
    assert "9 trials, expected 10" in capsys.readouterr().err
    assert not (tmp_path / "trials.md").exists()


def test_g1_trial_helpers():
    t = M.trials[4]  # T05: red -> box_a from (0.16, 0.04)
    assert isinstance(t, G1Trial) and t.plan().target == "red_cube"
    xyz = M.start_xyz(t, "red_cube", REG)
    assert xyz.tolist() == [0.16, 0.04, REG.object_rest_z("red_cube")]


def test_rules_check_the_tray_footprint_and_the_tcp_reach():  # (d)
    on_tray = list(M.trials)  # footprint overlaps box_a at (0.22, 0.09)
    on_tray[4] = dataclasses.replace(
        on_tray[4], start_xy_m={**on_tray[4].start_xy_m, "red_cube": (0.20, 0.06)}
    )
    assert any("overlaps box_a" in p for p in check_manifest(_with_trials(on_tray), REG))
    unreachable = list(M.trials)  # TCP at x = 0.1165, outside the work area
    unreachable[4] = dataclasses.replace(
        unreachable[4], start_xy_m={**unreachable[4].start_xy_m, "red_cube": (0.14, 0.0)}
    )
    assert any("not graspable" in p for p in check_manifest(_with_trials(unreachable), REG))


def test_stage_does_not_place_anything_when_the_reset_before_it_fails(runner, monkeypatch):
    t = M.trials[5]  # T06: blue -> box_b
    starts = {o: M.start_xyz(t, o, REG) for o in REG.objects}
    original = runner.check_reset

    def failing(pen=None, expected=None):
        check = original(pen, expected)
        check.passed = False
        check.reasons = ["injected: scene not at home"]
        return check

    monkeypatch.setattr(runner, "check_reset", failing)
    runner.last_reset = runner.reset()
    assert not runner.last_reset.passed
    check = runner.stage(starts)
    assert not check.passed and check.reasons[0].startswith("reset before staging failed")
    # the objects stayed at home: nothing was staged
    for o in REG.objects:
        home = np.asarray(REG.objects[o].home_xyz_m)
        assert np.linalg.norm(runner.gt.pose_of(o).position_m - home) <= C.RESET_OBJECT_POS_TOL_M
    # the evidence runner records such a trial without submitting anything
    start_check, rec = stage1_g1.run_trial(runner, M, t, True)
    assert not start_check.passed
    assert rec.outcome == "start_check_failed" and rec.commands_sent == 0 and rec.verdict is None
    assert rec.approval_source is None and rec.fixture_id == t.fixture
    tr = stage1_g1.TrialRecord(t, start_check, rec, "test", "参考")
    assert not tr.success and tr.result == "start_check_failed"
    json.dumps(tr.as_dict())
    monkeypatch.undo()
    assert runner.reset().passed


def test_run_trial_needs_the_holder_with_a_recorder(runner):
    with pytest.raises(ValueError, match="rec_holder"):
        stage1_g1.run_trial(runner, M, M.trials[0], True, recorder=object())
