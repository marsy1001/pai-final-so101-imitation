"""Judge one trial from recorded samples against execution.criteria.

Success is decided from the object pose/velocity and the gripper value only; the
grasp hold list is never read. One failure class per trial: the first that occurred.

The carry audit (Issue #4 固定 3/3, 2026-09-19) is part of the verdict, not a separate
report: between the lift and the true release the object must stay inside the grasp
zone and within CARRY_TCP_DIST_MAX_M of the TCP, and no equality constraint may still
be active after the release. The audit reads the object's position in the tip-site
frame and a count of active constraints - never the grasp hold list.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..plan import DestinationSpec, ObjectSpec
from ..sim.grasp import (
    GRASP_ZONE_X_M,
    GRASP_ZONE_Y_M,
    GRASP_ZONE_Z_MAX_M,
    GRASP_ZONE_Z_MIN_M,
)
from . import criteria as C


@dataclass(frozen=True)
class Sample:
    t: float
    obj_pos: np.ndarray
    obj_rot: np.ndarray
    obj_lin_v: np.ndarray
    obj_ang_v: np.ndarray
    gripper: float
    tcp_pos: np.ndarray
    # audit inputs, mandatory: the judge refuses (`unaudited`) a carry or release
    # sample that lacks them; it never falls back to the unaudited verdict
    obj_in_tip: np.ndarray  # object centre in the tip-site frame
    constraints_active: int  # active equality constraints in the model


@dataclass
class Verdict:
    success: bool
    failure: str | None
    max_lift_z: float
    lift_time: float | None
    release_time: float | None
    settle_time: float | None
    final_center: np.ndarray | None
    final_tilt_deg: float | None
    details: dict = field(default_factory=dict)


def tilt_deg(rot: np.ndarray) -> float:
    return float(np.degrees(np.arccos(np.clip(rot[2, 2], -1.0, 1.0))))


def bottom_corners_xy(pos: np.ndarray, rot: np.ndarray, size_m: float) -> np.ndarray:
    h = size_m / 2
    local = np.array([[sx * h, sy * h, -h] for sx in (-1, 1) for sy in (-1, 1)])
    return (pos + local @ rot.T)[:, :2]


def inside_tray(pos, rot, obj: ObjectSpec, dest: DestinationSpec) -> bool:
    cx, cy = dest.center_xy_m
    half = dest.inner_m / 2
    corners = bottom_corners_xy(pos, rot, obj.size_m)
    return bool(
        np.all(np.abs(corners[:, 0] - cx) <= half) and np.all(np.abs(corners[:, 1] - cy) <= half)
    )


def _missing_zone_input(s: Sample) -> bool:
    p = s.obj_in_tip
    return p is None or np.shape(p) != (3,) or not np.all(np.isfinite(p))


def _missing_constraint_input(s: Sample) -> bool:
    c = s.constraints_active
    return c is None or isinstance(c, bool) or not isinstance(c, (int, np.integer)) or c < 0


def zone_excursion_m(p: np.ndarray) -> float:
    """How far the point is outside the grasp zone, in metres (0.0 when inside)."""
    return max(
        0.0,
        abs(p[0]) - GRASP_ZONE_X_M,
        abs(p[1]) - GRASP_ZONE_Y_M,
        GRASP_ZONE_Z_MIN_M - p[2],
        p[2] - GRASP_ZONE_Z_MAX_M,
    )


def _first_sustained(
    samples: list[Sample], pred, hold_s: float, start_t: float = 0.0
) -> float | None:
    """Time at which `pred` has held continuously for `hold_s` (None if never)."""
    run_start = None
    for s in samples:
        if s.t < start_t:
            continue
        if pred(s):
            if run_start is None:
                run_start = s.t
            if s.t - run_start >= hold_s:
                return s.t
        else:
            run_start = None
    return None


def judge(
    samples: list[Sample],
    obj: ObjectSpec,
    dest: DestinationSpec,
    other_dests: list[DestinationSpec],
    *,
    pick_cmd_t: float,
    release_t: float | None,
    rejected: str | None = None,
) -> Verdict:
    max_lift = max((s.obj_pos[2] for s in samples), default=0.0)
    v = Verdict(False, None, max_lift, None, release_t, None, None, None)
    if rejected:
        v.failure, v.details["reason"] = "rejected", rejected
        return v
    deadline = pick_cmd_t + C.TIMEOUT_S
    last_t = samples[-1].t if samples else pick_cmd_t

    def fail(cls: str, at_t: float | None, **details) -> Verdict:
        # "first failure that occurred": a failure observed only at or after the
        # deadline is reported as timeout, because the deadline came first
        if at_t is None or at_t >= deadline:
            v.failure = "timeout"
            v.details["deadline_s"] = deadline
            v.details["would_be"] = cls
        else:
            v.failure = cls
        v.details["at_s"] = at_t
        v.details.update(details)
        return v

    def lifted(s: Sample) -> bool:
        return s.obj_pos[2] >= C.LIFT_CENTER_Z_M and s.gripper <= C.GRIPPER_CLOSED_MAX

    def settled(s: Sample) -> bool:
        return bool(
            np.linalg.norm(s.obj_lin_v) < C.SETTLE_LIN_V_MAX_M_S
            and np.linalg.norm(s.obj_ang_v) < C.SETTLE_ANG_V_MAX_RAD_S
            and s.gripper >= C.GRIPPER_OPEN_MIN
        )

    # pick: lifted with the gripper closed, sustained
    v.lift_time = _first_sustained(samples, lifted, C.LIFT_HOLD_S, pick_cmd_t)
    if v.lift_time is None:
        # no lift is observed once the trial ends (release or last sample)
        return fail("no_lift", release_t if release_t is not None else last_t)
    if release_t is None:
        return fail("dropped", last_t)
    # true release (fixed 2026-09-19): the first tick after the lift at which the
    # measured jaws are open. `release_t` is the trace label, logged one gripper wait
    # later; it is kept in the details but no window is anchored on it any more.
    open_t = next(
        (s.t for s in samples if s.t > v.lift_time and s.gripper >= C.GRIPPER_OPEN_MIN), None
    )
    release_eff = open_t if open_t is not None else release_t
    v.release_time = release_eff
    v.details["release_trace_s"] = release_t
    v.details["release_open_s"] = open_t

    # carry: separation from the gripper (dropped) and the containment audit A/B,
    # scanned in time order so the earliest of the two is the reported failure
    at_lift = next(s for s in samples if s.t >= v.lift_time)
    d0 = float(np.linalg.norm(at_lift.obj_pos - at_lift.tcp_pos))
    # The audit is mandatory: every carry sample needs the object in the tip frame
    # and every sample from the release on needs the constraint count. A missing or
    # invalid input at any tick is `unaudited`, never a pass.
    carry = [s for s in samples if v.lift_time <= s.t < release_eff]
    if not carry:
        return fail("unaudited", release_eff, audit="grasp_zone", reason="no carry samples")
    max_dist, max_excursion = 0.0, 0.0
    for s in carry:
        dist = float(np.linalg.norm(s.obj_pos - s.tcp_pos))
        max_dist = max(max_dist, dist)
        if dist > d0 + C.DROP_SEPARATION_M:
            return fail("dropped", s.t, dropped_at=s.t, tcp_distance_m=dist)
        if dist > C.CARRY_TCP_DIST_MAX_M:
            return fail("not_contained", s.t, audit="tcp_distance", tcp_distance_m=dist)
        if _missing_zone_input(s):
            return fail("unaudited", s.t, audit="grasp_zone", reason="obj_in_tip missing")
        excursion = zone_excursion_m(s.obj_in_tip)
        max_excursion = max(max_excursion, excursion)
        if excursion > 0.0:
            return fail(
                "not_contained",
                s.t,
                audit="grasp_zone",
                zone_excursion_m=excursion,
                obj_in_tip_m=[float(c) for c in s.obj_in_tip],
            )
    v.details["carry_max_tcp_distance_m"] = max_dist
    v.details["carry_max_zone_excursion_m"] = max_excursion
    v.details["carry_samples_audited"] = len(carry)

    # audit C: nothing may still constrain the object once the jaws are open
    after = [s for s in samples if s.t >= release_eff]
    if not after:
        return fail("unaudited", release_eff, audit="constraints", reason="no release samples")
    for s in after:
        if _missing_constraint_input(s):
            return fail("unaudited", s.t, audit="constraints", reason="constraints_active missing")
        if s.constraints_active > 0:
            return fail("still_constrained", s.t, constraints_active=int(s.constraints_active))
    v.details["release_samples_audited"] = len(after)

    if release_eff >= deadline:
        return fail("not_settled", release_eff)
    # settle within the window after the true release
    window = [s for s in samples if release_eff <= s.t <= release_eff + C.SETTLE_WINDOW_S]
    v.settle_time = _first_sustained(window, settled, C.SETTLE_HOLD_S, release_eff)
    final = window[-1] if window else samples[-1]
    v.final_center, v.final_tilt_deg = final.obj_pos.copy(), tilt_deg(final.obj_rot)
    if v.settle_time is None:
        # not settled is observed at the end of the window (or the deadline, if earlier)
        return fail("not_settled", release_eff + C.SETTLE_WINDOW_S)
    if v.settle_time >= deadline:
        return fail("not_settled", v.settle_time)
    at = next(s for s in window if s.t >= v.settle_time)
    v.final_center, v.final_tilt_deg = at.obj_pos.copy(), tilt_deg(at.obj_rot)
    for od in other_dests:
        if inside_tray(at.obj_pos, at.obj_rot, obj, od):
            return fail("wrong_destination", v.settle_time, landed_in=od.id)
    seated = (
        inside_tray(at.obj_pos, at.obj_rot, obj, dest)
        and abs(at.obj_pos[2] - C.SEAT_CENTER_Z_M) <= C.SEAT_Z_TOL_M
        and v.final_tilt_deg <= C.TILT_MAX_DEG
    )
    if not seated:
        return fail(
            "not_seated", v.settle_time, inside=inside_tray(at.obj_pos, at.obj_rot, obj, dest)
        )
    v.success = True
    return v
