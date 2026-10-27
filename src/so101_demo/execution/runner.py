"""Plan runner: the single entry from a contract JSON to the sim (demo-plan 4a 実行側).

States: IDLE -> RUNNING -> DONE -> RESETTING -> IDLE (the enum values carry the labels
shown on screen). A rejection before any motion goes DONE -> RESETTING -> IDLE; a safety
refusal, a failure, an unexpected error or a stop during the run goes through RUNNING
first. A submission while RUNNING is answered `busy` with no transition at all. Every
transition is logged with the sim time and the wall clock.

Approval: the runner executes an `ApprovedPlan` only. Two functions create one:
`approve_for_development` (dev fixed fixtures, needs an explicit flag; the CLI
`evidence.stage1_g1` exposes it as `--approve-fixed-fixture`) and `approve_from_review`
(the stage-2 approval screen, 3b). A raw dict, a JSON string or a `Plan` submitted
directly is rejected without a single robot command. There is no third path.

The judgement is 0b's (`execution.judge`, values in `execution.criteria`); the reset
check adds the values fixed on Issue #9 (`criteria.RESET_*`). The check runs after every
reset; `RunRecord.reset_previous` is the result of the reset that preceded the run (the
scene may legitimately differ at submit time, e.g. G1 trials start objects away from
home), and `scene_at_submit` records where every object actually was.
"""

from __future__ import annotations

import datetime as dt
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import numpy as np

from ..kinematics import So101Kinematics, solve_top_down
from ..locate import SimGroundTruth
from ..plan import Plan, PlanInvalid, Registry, load_registry, validate_plan
from ..sim.grasp import SimpleGrasp
from ..sim.model import So101Sim
from ..sim.scene import SceneConfig, load_scene, penetrations, set_object_pose
from . import criteria as C
from .judge import Verdict, judge, tilt_deg
from .primitives import CONTROL_DT_S, GraspParams, PickPlace, TrialTimeout
from .safety import SafetyLimits, SafetyRejected
from .trial import HOME_XYZ_M, RecordingBackend

DEV_APPROVAL_SOURCE = "dev-fixed-fixture"
REVIEW_APPROVAL_SOURCE = "approval-screen"
APPROVAL_SOURCES = (DEV_APPROVAL_SOURCE, REVIEW_APPROVAL_SOURCE)


class RunnerState(StrEnum):
    IDLE = "待機"
    RUNNING = "実行中"
    DONE = "完了"
    RESETTING = "リセット"


@dataclass(frozen=True)
class ApprovedPlan:
    """A validated, executable plan plus who approved it. Build one only through
    `approve_for_development` or `approve_from_review`."""

    plan: Plan
    source: str  # one of APPROVAL_SOURCES
    reference: str  # fixture id, or the approval screen's run id
    approved_at: str  # ISO 8601, wall clock

    def __post_init__(self) -> None:
        if self.source not in APPROVAL_SOURCES:
            raise ValueError(f"unknown approval source {self.source!r}")
        if not self.plan.executable:
            raise ValueError(f"only status=ok plans can be approved, got {self.plan.status!r}")
        recheck_plan(self.plan)  # a Plan can be built or `replace`d by hand; approval re-validates


def recheck_plan(plan: Plan) -> None:
    """Re-run the contract validation on a `Plan` object (schema, registered ids, the
    target/steps cross-field rule). `Plan` is a plain dataclass, so an approved plan is
    checked again here and once more by the runner right before the first command."""
    if validate_plan(_plan_dict(plan)) != plan:
        raise PlanInvalid("plan does not round-trip through the contract")


def parse_plan(text: str) -> Plan:
    """JSON text -> validated Plan. Syntax errors and schema violations both raise
    PlanInvalid so the runner records one rejection class for bad input."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise PlanInvalid(f"not JSON: {e.msg} at line {e.lineno} column {e.colno}") from e
    return validate_plan(data)


def approve_for_development(
    plan: Plan, fixture_id: str, *, approve_fixed_fixture: bool
) -> ApprovedPlan:
    """Mark a dev fixed fixture as approved. `approve_fixed_fixture` must be passed
    explicitly and be True; it is the CLI flag, so a fixture never runs by default."""
    if approve_fixed_fixture is not True:
        raise PermissionError(
            "development approval needs the explicit flag --approve-fixed-fixture (unapproved)"
        )
    return ApprovedPlan(plan, DEV_APPROVAL_SOURCE, fixture_id, _now())


def approve_from_review(plan: Plan, run_id: str) -> ApprovedPlan:
    """Approval by a person on the approval screen (stage 2, 3b). The screen decides
    what it shows and re-validates; this only records the approval on the plan."""
    return ApprovedPlan(plan, REVIEW_APPROVAL_SOURCE, run_id, _now())


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Transition:
    sim_time_s: float
    wall_time: str
    state: RunnerState
    detail: str = ""


@dataclass
class ResetCheck:
    passed: bool
    object_pos_err_m: dict[str, float]
    object_tilt_deg: dict[str, float]
    joint_err_deg: float
    gripper: float
    held: str | None
    constraints_active: int
    penetrations: list
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "passed": self.passed,
            "object_pos_err_mm": {k: v * 1000 for k, v in self.object_pos_err_m.items()},
            "object_tilt_deg": self.object_tilt_deg,
            "joint_err_deg": self.joint_err_deg,
            "gripper": self.gripper,
            "held": self.held,
            "constraints_active": self.constraints_active,
            "penetrations": [list(p) for p in self.penetrations],
            "reasons": list(self.reasons),
        }


@dataclass
class RunRecord:
    """One submission. `outcome` is `success`, a judge failure class, `rejected` (never
    ran: unapproved / invalid plan / safety refusal), `busy` (another plan was running),
    `stopped` (stop() during the run), `error` (an unexpected exception; the runner
    still resets and returns to IDLE) or `reset_failed` (pick/place judged success but
    the reset afterwards did not pass; never counted as a pass)."""

    submitted_at: str
    fixture_id: str | None
    approval_source: str | None
    plan: dict | None
    config_id: str
    outcome: str
    reason: str | None
    commands_sent: int  # joint / gripper / stop commands the backend received in this run
    transitions: list[Transition]
    verdict: Verdict | None
    reset_previous: ResetCheck | None  # result of the reset that preceded this submission
    reset_after: ResetCheck | None
    scene_at_submit: dict[str, list[float]]  # object positions (m) when submit() was called
    other_objects_max_displacement_m: dict[str, float]
    sim_time_s: float
    wall_time_s: float
    trace: list[tuple[float, str]] = field(default_factory=list)
    grasp_events: list[dict] = field(default_factory=list)
    observer_error: str | None = None  # first failure of the observer (recorder) in this run
    video_path: str | None = None
    video_bytes: int | None = None

    def as_dict(self) -> dict:
        v = self.verdict
        return {
            "submitted_at": self.submitted_at,
            "fixture_id": self.fixture_id,
            "approval_source": self.approval_source,
            "plan": self.plan,
            "config_id": self.config_id,
            "outcome": self.outcome,
            "reason": self.reason,
            "commands_sent": self.commands_sent,
            "transitions": [
                {
                    "sim_time_s": t.sim_time_s,
                    "wall": t.wall_time,
                    "state": t.state.value,
                    "detail": t.detail,
                }
                for t in self.transitions
            ],
            "verdict": None
            if v is None
            else {
                "success": v.success,
                "failure": v.failure,
                "max_lift_z_m": v.max_lift_z,
                "lift_time_s": v.lift_time,
                "release_time_s": v.release_time,
                "settle_time_s": v.settle_time,
                "final_center_m": None if v.final_center is None else v.final_center.tolist(),
                "final_tilt_deg": v.final_tilt_deg,
                "details": _jsonable(v.details),
            },
            "reset_previous": None
            if self.reset_previous is None
            else self.reset_previous.as_dict(),
            "reset_after": None if self.reset_after is None else self.reset_after.as_dict(),
            "scene_at_submit_m": self.scene_at_submit,
            "other_objects_max_displacement_mm": {
                k: v * 1000 for k, v in self.other_objects_max_displacement_m.items()
            },
            "sim_time_s": self.sim_time_s,
            "wall_time_s": self.wall_time_s,
            "trace": [[t, label] for t, label in self.trace],
            "grasp_events": list(self.grasp_events),
            "observer_error": self.observer_error,
            "video": None
            if self.video_path is None
            else {"file": self.video_path, "bytes": self.video_bytes},
        }


def _jsonable(x):
    if isinstance(x, dict):
        return {k: _jsonable(v) for k, v in x.items()}
    if isinstance(x, list | tuple):
        return [_jsonable(v) for v in x]
    if isinstance(x, np.ndarray):
        return x.tolist()
    if isinstance(x, np.generic):
        return x.item()
    return x


class PlanRunner:
    """Owns one scene with every registered object and runs approved plans one at a time.

    `observer(sim)` (optional) is called once with the loaded scene and returns a
    per-tick callback (e.g. a video recorder). `on_tick(runner)` (optional) is called
    after every control tick while a plan runs; tests use it to call `stop()` or to
    move an object mid-run. Neither is part of the plan path.
    """

    def __init__(
        self,
        config_id: str = "sim-v1-simple",
        registry: Registry | None = None,
        *,
        observer: Callable[[So101Sim], Callable[[So101Sim], None]] | None = None,
        styled: bool = False,
    ) -> None:
        self.registry = registry or load_registry()
        self.config = SceneConfig(config_id, tuple(self.registry.objects))
        self.config_id = config_id
        self.sim = load_scene(self.config, self.registry, styled=styled)  # visual only
        self.kin = So101Kinematics(self.sim.arm_limits_rad())
        self.home_q_deg = solve_top_down(self.kin, HOME_XYZ_M)
        self.grasp = (
            SimpleGrasp(self.sim, self.config.objects) if self.config.simplified_grasp else None
        )
        self.gt = SimGroundTruth(self.sim, self.config.objects)
        self.backend = RecordingBackend(self.sim, self.grasp, home_q_deg=self.home_q_deg)
        self._observer_cb = observer(self.sim) if observer is not None else None
        self._observer_error: str | None = None
        self.on_tick: Callable[[PlanRunner], None] | None = None
        self.state = RunnerState.IDLE
        self.transitions: list[Transition] = []
        self.records: list[RunRecord] = []
        self._pp: PickPlace | None = None
        self._stop_requested = False
        self._log(RunnerState.IDLE, "runner created")
        self.last_reset = self.reset()

    # --- state -------------------------------------------------------------------
    def _log(self, state: RunnerState, detail: str = "") -> Transition:
        self.state = state
        t = Transition(float(self.sim.data.time), _now(), state, detail)
        self.transitions.append(t)
        return t

    def _tick_hook(self, sim: So101Sim) -> None:
        # The observer (video recorder) is not part of the plan path: its first failure in
        # a run is recorded on the run and it is muted until the next submit. Physics and
        # the judgement continue. The scenario hook `on_tick` is different: it stands in
        # for the caller, so its exceptions end the run as `error`.
        if self._observer_cb is not None and self._observer_error is None:
            try:
                self._observer_cb(sim)
            except Exception as e:  # noqa: BLE001 - recorded, never fatal
                self._observer_error = f"{type(e).__name__}: {e}"
        if self.on_tick is not None:
            self.on_tick(self)

    # --- reset -------------------------------------------------------------------
    def reset(self) -> ResetCheck:
        """リセット: home posture, jaws open, nothing held, every object at its home pose,
        settle, then check against criteria.RESET_*. Always returns to 待機."""
        self._log(RunnerState.RESETTING)
        self._pp = None
        self._stop_requested = False
        self.backend.reset()  # clears the command log and any owner token
        homes = {oid: o.home_xyz_m for oid, o in self.registry.objects.items()}
        pen = self._place_and_settle(homes)
        check = self.check_reset(pen)
        self._log(
            RunnerState.IDLE,
            "reset " + ("complete" if check.passed else "FAILED: " + "; ".join(check.reasons)),
        )
        return check

    def _place_and_settle(self, positions: dict[str, Any]) -> list:
        """Put every object at `positions` (id -> xyz, registered yaw), settle for
        RESET_SETTLE_S with no observer or hook, clear the sample logs; returns the
        penetrations found right after placing (before settling)."""
        for oid, o in self.registry.objects.items():
            set_object_pose(self.sim, oid, positions[oid], o.yaw_deg)
        pen = penetrations(self.sim, min_depth_m=1e-3)
        self.backend.on_tick = None  # physical placement only: no observer, no hook can break it
        self.backend.tick(C.RESET_SETTLE_S)
        self.backend.samples.clear()
        self.backend.contact_log.clear()
        return pen

    def check_reset(
        self, pen: list | None = None, expected: dict[str, Any] | None = None
    ) -> ResetCheck:
        """Scene check against criteria.RESET_*. `expected` (object id -> xyz) replaces the
        registered home poses; `stage()` uses it for trial start positions (G1)."""
        reasons: list[str] = []
        pos_err, tilts = {}, {}
        where = "home" if expected is None else "start"
        for oid, o in self.registry.objects.items():
            p = self.gt.pose_of(oid)
            want = o.home_xyz_m if expected is None else expected[oid]
            pos_err[oid] = float(np.linalg.norm(p.position_m - np.asarray(want, dtype=float)))
            tilts[oid] = tilt_deg(p.rotation)
            if pos_err[oid] > C.RESET_OBJECT_POS_TOL_M:
                reasons.append(f"{oid} {pos_err[oid] * 1000:.2f} mm from {where}")
            if tilts[oid] > C.RESET_OBJECT_TILT_MAX_DEG:
                reasons.append(f"{oid} tilted {tilts[oid]:.2f} deg")
        st = self.backend.state()
        joint_err = float(np.max(np.abs(np.asarray(st.joints_deg) - self.home_q_deg)))
        if joint_err > C.RESET_JOINT_TOL_DEG:
            reasons.append(f"joints {joint_err:.2f} deg from home")
        if st.gripper < C.GRIPPER_OPEN_MIN:
            reasons.append(f"gripper {st.gripper:.1f} not open")
        held = self.grasp.held if self.grasp is not None else None
        if held is not None:
            reasons.append(f"still holding {held}")
        eq = int(np.count_nonzero(self.sim.data.eq_active))
        if eq > 0:
            reasons.append(f"{eq} equality constraint(s) active")
        pen = penetrations(self.sim, min_depth_m=1e-3) if pen is None else pen
        if pen:
            reasons.append(f"initial penetration {pen}")
        return ResetCheck(
            not reasons, pos_err, tilts, joint_err, st.gripper, held, eq, pen, reasons
        )

    # --- scenario helpers (never on the plan path) ---------------------------------
    def place_object(self, object_id: str, xyz, yaw_deg: float = 0.0) -> None:
        """Move an object in the scene before or during a run (rejection scenarios: out of
        the work area, displaced before the close). Not reachable from a plan."""
        set_object_pose(self.sim, object_id, xyz, yaw_deg)

    def stage(self, start_xyz: dict[str, Any]) -> ResetCheck:
        """Put every object at its trial start position (G1 manifests start the target away
        from home), settle for RESET_SETTLE_S and check the scene against those positions
        with the reset tolerances. Only when idle after a passing reset; the check result is
        the trial's "start check". Not reachable from a plan."""
        if self.state != RunnerState.IDLE:
            raise RuntimeError(f"stage() needs an idle runner, state is {self.state.value}")
        if self.last_reset is None or not self.last_reset.passed:
            self.last_reset = self.reset()
            if not self.last_reset.passed:
                # nothing is placed: a trial must not start from an unknown scene
                failed = self.last_reset
                return ResetCheck(
                    False,
                    failed.object_pos_err_m,
                    failed.object_tilt_deg,
                    failed.joint_err_deg,
                    failed.gripper,
                    failed.held,
                    failed.constraints_active,
                    failed.penetrations,
                    ["reset before staging failed: " + "; ".join(failed.reasons)],
                )
        pen = self._place_and_settle(start_xyz)
        return self.check_reset(pen, expected=start_xyz)

    def stop(self) -> None:
        """Stop the current run: pending motion is dropped, the robot freezes, the run
        ends as `stopped` and the runner resets. No effect when idle."""
        if self._pp is not None:
            self._stop_requested = True
            self._pp.stop()

    # --- the single entry ----------------------------------------------------------
    def submit(self, submission: Any, fixture_id: str | None = None) -> RunRecord:
        """Run one approved plan. Anything that is not an `ApprovedPlan` is rejected
        (`unapproved` / `invalid_plan`) and moves the robot not at all; the runner still
        goes through リセット so the scene is known to be at home afterwards."""
        wall0 = time.perf_counter()
        sim0 = float(self.sim.data.time)
        submitted_at = _now()
        n_before = len(self.transitions)
        scene = {
            o: [float(c) for c in self.gt.pose_of(o).position_m] for o in self.registry.objects
        }
        if self.state != RunnerState.IDLE:
            # no state change, no reset: the running plan is left alone
            rec = self._rejected(
                submitted_at,
                fixture_id,
                submission,
                "busy",
                f"runner is {self.state.value}",
                sim0,
                wall0,
                n_before,
                scene,
                reset=False,
            )
            return rec

        approved, reason = self._as_approved(submission)
        if approved is None:
            return self._rejected(
                submitted_at,
                fixture_id,
                submission,
                "rejected",
                reason,
                sim0,
                wall0,
                n_before,
                scene,
            )
        plan = approved.plan
        fixture_id = fixture_id or approved.reference
        try:
            recheck_plan(plan)
        except PlanInvalid as e:
            return self._rejected(
                submitted_at,
                fixture_id,
                submission,
                "rejected",
                f"invalid_plan: {e}",
                sim0,
                wall0,
                n_before,
                scene,
            )

        # the gate is the previous reset, not the scene now: objects may legitimately
        # start away from home (G1 trials); where they are is in `scene_at_submit`
        reset_previous = self.last_reset
        if reset_previous is None or not reset_previous.passed:
            reset_previous = self.reset()
            if not reset_previous.passed:
                return self._rejected(
                    submitted_at,
                    fixture_id,
                    submission,
                    "rejected",
                    "reset check failed before run: " + "; ".join(reset_previous.reasons),
                    sim0,
                    wall0,
                    n_before,
                    scene,
                )

        target, dest = plan.target, plan.destination
        others = [o for o in self.registry.objects if o != target]
        self._log(RunnerState.RUNNING, f"{fixture_id} {target} -> {dest} ({approved.source})")
        self.backend.attach(self.gt, target)
        self._observer_error = None
        self.backend.on_tick = self._tick_hook
        self.backend.samples.clear()
        self.backend.contact_log.clear()
        home_pos = {o: self.gt.pose_of(o).position_m.copy() for o in others}
        max_disp = dict.fromkeys(others, 0.0)

        def track(_runner):
            for o in others:
                d = float(np.linalg.norm(self.gt.pose_of(o).position_m - home_pos[o]))
                if d > max_disp[o]:
                    max_disp[o] = d

        user_hook = self.on_tick

        def hook(r):
            track(r)
            if user_hook is not None:
                user_hook(r)

        self.on_tick = hook
        token = self.backend.acquire("runner")
        pick_t = float(self.sim.data.time)
        n_events = 0 if self.grasp is None else len(self.grasp.events)
        deadline = pick_t + C.TIMEOUT_S
        pp = PickPlace(
            self.backend,
            self.kin,
            self.gt,
            self.registry,
            SafetyLimits(self.sim.arm_limits_rad()),
            token,
            grasp=GraspParams.for_config(self.config_id),
            deadline_s=deadline,
        )
        self._pp = pp
        rejected = None
        release_t = None
        error = None
        try:
            for step in plan.steps:
                if step.action == "pick":
                    pp.pick(step.object)
                elif step.action == "place":
                    pp.place(step.destination, target)
                else:  # the schema forbids it; belt and braces
                    raise SafetyRejected("unknown_action", step.action)
            release_t = next(t for t, label in pp.trace if label == "place:released")
            self.backend.tick(
                min(
                    C.SETTLE_WINDOW_S + C.SETTLE_HOLD_S,
                    max(deadline - self.sim.data.time, CONTROL_DT_S),
                )
            )
        except SafetyRejected as e:
            rejected = str(e)
        except TrialTimeout:
            pass
        except Exception as e:  # noqa: BLE001 - anything else: record, reset, return to IDLE
            error = f"{type(e).__name__}: {e}"
            rejected = error
        finally:
            self.on_tick = user_hook
        commands = len(self.backend.command_log)
        if self.backend._owner == token:
            self.backend.release(token)
        d = self.registry.destinations[dest]
        other_d = [x for k, x in self.registry.destinations.items() if k != dest]
        verdict = judge(
            self.backend.samples,
            self.registry.objects[target],
            d,
            other_d,
            pick_cmd_t=pick_t,
            release_t=release_t,
            rejected=rejected,
        )
        if error is not None:
            outcome, reason = "error", error
        elif self._stop_requested:
            outcome, reason = "stopped", rejected
        elif verdict.success:
            outcome, reason = "success", None
        else:
            outcome, reason = verdict.failure, verdict.details.get("reason")
        self._log(RunnerState.DONE, f"{outcome}" + (f": {reason}" if reason else ""))
        run_sim_s = float(self.sim.data.time) - pick_t
        observer_error = self._observer_error
        trace = list(pp.trace)
        events = (
            []
            if self.grasp is None
            else [
                {
                    "time_s": ev.time_s,
                    "action": ev.action,
                    "object": ev.object_id,
                    "candidates": list(ev.candidates),
                }
                for ev in self.grasp.events[n_events:]
            ]
        )
        self._pp = None
        reset_after = self.reset()
        self.last_reset = reset_after
        if outcome == "success" and not reset_after.passed:
            # criteria: a run whose reset check fails is not a pass (Issue #9)
            outcome, reason = "reset_failed", "; ".join(reset_after.reasons)
        rec = RunRecord(
            submitted_at,
            fixture_id,
            approved.source,
            _plan_dict(plan),
            self.config_id,
            outcome,
            reason,
            commands,
            self.transitions[n_before:],
            verdict,
            reset_previous,
            reset_after,
            scene,
            max_disp,
            run_sim_s,
            time.perf_counter() - wall0,
            trace,
            events,
            observer_error=observer_error,
        )
        self.records.append(rec)
        return rec

    def _as_approved(self, submission: Any) -> tuple[ApprovedPlan | None, str | None]:
        if isinstance(submission, ApprovedPlan):
            return submission, None
        if isinstance(submission, Plan):
            return None, "unapproved: a Plan without approval"
        if isinstance(submission, str | bytes):
            try:
                text = submission if isinstance(submission, str) else submission.decode()
                parse_plan(text)
            except UnicodeDecodeError as e:
                return None, f"invalid_plan: not UTF-8 text ({e.reason})"
            except PlanInvalid as e:
                return None, f"invalid_plan: {e}"
            return None, "unapproved: JSON text without approval"
        if isinstance(submission, dict):
            try:
                validate_plan(submission)
            except PlanInvalid as e:
                return None, f"invalid_plan: {e}"
            return None, "unapproved: a dict without approval"
        return None, f"unapproved: {type(submission).__name__} is not an ApprovedPlan"

    def _rejected(
        self,
        submitted_at,
        fixture_id,
        submission,
        outcome,
        reason,
        sim0,
        wall0,
        n_before,
        scene,
        *,
        reset=True,
    ) -> RunRecord:
        # a busy rejection sends nothing; the log then belongs to the plan that is running
        commands = 0 if not reset else len(self.backend.command_log)
        plan = None
        if isinstance(submission, ApprovedPlan):
            plan = _plan_dict(submission.plan)
        elif isinstance(submission, Plan):
            plan = _plan_dict(submission)
        elif isinstance(submission, dict):
            plan = submission
        elif isinstance(submission, str | bytes):
            plan = {"raw": repr(submission[:200])}
        reset_after = None
        if reset:
            self._log(RunnerState.DONE, f"{outcome}: {reason}")
            reset_after = self.reset()
            self.last_reset = reset_after
        rec = RunRecord(
            submitted_at,
            fixture_id,
            submission.source if isinstance(submission, ApprovedPlan) else None,
            plan,
            self.config_id,
            outcome,
            reason,
            commands,
            self.transitions[n_before:],
            None,
            None,
            reset_after,
            scene,
            {},
            0.0,  # never ran; the reset zeroes the sim clock
            time.perf_counter() - wall0,
        )
        self.records.append(rec)
        return rec


def _plan_dict(plan: Plan) -> dict:
    return {
        "status": plan.status,
        "target": plan.target,
        "destination": plan.destination,
        "steps": [
            {
                k: v
                for k, v in (
                    ("action", s.action),
                    ("object", s.object),
                    ("destination", s.destination),
                )
                if v is not None
            }
            for s in plan.steps
        ],
        "note": plan.note,
    }
