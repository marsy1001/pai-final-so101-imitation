"""The fixed G1 trial manifest (contracts/fixtures/g1/manifest.json, Issue #19).

Ten trials, each naming a 4a fixture (the plan that is submitted), the target and
destination that plan must have, and where every registered object starts. The manifest
is frozen before the G1 evaluation (its commit hash is recorded on the gate Issue #13)
and is never rewritten afterwards; a change is a new manifest with its reason on #13.

`check_manifest` returns every rule the file breaks (empty = valid). The rules are the
XPS acceptance criteria of Issue #19: (a) each plan validates against the contract and
matches the trial's target / destination, (b) each of the four combinations appears at
least twice, (c) consecutive trials switch the target, (d) every start of every object
lies in the work area, the two objects are at least MIN_CENTRE_DISTANCE_M apart, neither
cube sits on a tray and the grasp TCP (along the object's yaw) is inside the work area
(`start_violations`), (e) trial ids are unique.

2026-09-21: both objects get a start position per trial, chosen by
the user from the rendered layout, so the earlier rule "only the target leaves home" and
the named positions P1 / P2 (`start_label`, `positions_m`) are gone. The geometric rules
now apply to both objects.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ..kinematics.top_down import WORKSPACE_MAX, WORKSPACE_MIN, workspace_contains
from .paths import FIXTURES_4A_DIR, G1_MANIFEST_PATH
from .registry import Registry, load_registry
from .schema import Plan, PlanInvalid, validate_plan

TRIAL_COUNT = 10
MIN_REPEATS_PER_COMBINATION = 2
# 40 mm cube + the reach of the moving jaw, which opens radially (Issue #9, 2026-09-19)
MIN_CENTRE_DISTANCE_M = 0.060


@dataclass(frozen=True)
class G1Trial:
    trial: str  # "T01" .. "T10"
    fixture: str  # stem of a file in contracts/fixtures/4a
    target: str
    destination: str
    start_xy_m: dict[str, tuple[float, float]]  # every registered object

    @property
    def fixture_path(self) -> Path:
        return FIXTURES_4A_DIR / f"{self.fixture}.json"

    def plan(self) -> Plan:
        return validate_plan(json.loads(self.fixture_path.read_text(encoding="utf-8")))


@dataclass(frozen=True)
class G1Manifest:
    path: Path
    config_id: str
    trials: tuple[G1Trial, ...]

    def start_xyz(self, trial: G1Trial, object_id: str, registry: Registry) -> np.ndarray:
        x, y = trial.start_xy_m[object_id]
        return np.array([x, y, registry.object_rest_z(object_id)])


def load_g1_manifest(path: Path = G1_MANIFEST_PATH) -> G1Manifest:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    trials = tuple(
        G1Trial(
            t["trial"],
            t["fixture"],
            t["target"],
            t["destination"],
            {k: (float(v[0]), float(v[1])) for k, v in t["start_xy_m"].items()},
        )
        for t in raw["trials"]
    )
    return G1Manifest(Path(path), raw["config_id"], trials)


def in_work_area_xy(xy) -> bool:
    x, y = float(xy[0]), float(xy[1])
    return bool(
        WORKSPACE_MIN[0] - 1e-9 <= x <= WORKSPACE_MAX[0] + 1e-9
        and WORKSPACE_MIN[1] - 1e-9 <= y <= WORKSPACE_MAX[1] + 1e-9
    )


def start_violations(reg: Registry, object_id: str, xy, others_xy: dict[str, Any]) -> list[str]:
    """Geometric rules a start position of `object_id` must satisfy (Issue #19):
      area  the centre is inside the work area
      dist  centre-to-centre distance to every other object >= MIN_CENTRE_DISTANCE_M
      tray  the cube footprint does not overlap a tray's outer footprint
      tcp   the grasp TCP (half cube + pocket clearance from the centre along the object's
            registered yaw, i.e. toward -x at yaw 0; Issue #21) is inside the work area,
            so the top-down IK does not refuse it as outside_workspace
    Returns the tags of the rules broken (empty = fine)."""
    # execution.primitives imports this package; import lazily to avoid the cycle
    from ..execution.primitives import POCKET_CLEARANCE_M, grasp_tcp_for

    obj = reg.objects[object_id]
    p = np.array([float(xy[0]), float(xy[1]), reg.object_rest_z(object_id)])
    out: list[str] = []
    if not in_work_area_xy(p[:2]):
        out.append("area")
    for oid, oxy in others_xy.items():
        if oid == object_id:
            continue
        if np.linalg.norm(p[:2] - np.asarray(oxy, dtype=float)[:2]) < MIN_CENTRE_DISTANCE_M - 1e-9:
            out.append("dist")
            break
    half = obj.size_m / 2
    for did, d in reg.destinations.items():
        cx, cy = d.center_xy_m
        reach = half + d.outer_half_m - 1e-9
        if abs(p[0] - cx) < reach and abs(p[1] - cy) < reach:
            out.append(f"tray:{did}")
    if not workspace_contains(grasp_tcp_for(p, obj.size_m, POCKET_CLEARANCE_M, obj.yaw_deg)):
        out.append("tcp")
    return out


def check_manifest(m: G1Manifest, registry: Registry | None = None) -> list[str]:
    """Every rule the manifest breaks, as one line each. Empty means valid."""
    reg = registry or load_registry()
    problems: list[str] = []
    if len(m.trials) != TRIAL_COUNT:
        problems.append(f"{len(m.trials)} trials, expected {TRIAL_COUNT}")
    ids = [t.trial for t in m.trials]
    if len(set(ids)) != len(ids):
        problems.append(f"duplicate trial ids: {sorted({i for i in ids if ids.count(i) > 1})}")
    combos: dict[tuple[str, str], int] = {(o, d): 0 for o in reg.objects for d in reg.destinations}
    for t in m.trials:
        # (a) the plan validates and matches the trial
        if not t.fixture_path.is_file():
            problems.append(f"{t.trial}: fixture {t.fixture_path} missing")
        else:
            try:
                plan = t.plan()
            except PlanInvalid as e:
                problems.append(f"{t.trial}: fixture {t.fixture} invalid: {e}")
            else:
                if not plan.executable:
                    problems.append(f"{t.trial}: fixture {t.fixture} is not status=ok")
                if (plan.target, plan.destination) != (t.target, t.destination):
                    problems.append(
                        f"{t.trial}: fixture {t.fixture} is {plan.target} -> {plan.destination}, "
                        f"trial says {t.target} -> {t.destination}"
                    )
        if t.target not in reg.objects:
            problems.append(f"{t.trial}: unregistered target {t.target!r}")
        if t.destination not in reg.destinations:
            problems.append(f"{t.trial}: unregistered destination {t.destination!r}")
        if (t.target, t.destination) in combos:
            combos[(t.target, t.destination)] += 1
        # (d) start positions: work area, distance, tray footprint, graspable TCP
        if set(t.start_xy_m) != set(reg.objects) or t.target not in t.start_xy_m:
            problems.append(f"{t.trial}: start_xy_m must name every registered object")
            continue
        for oid, xy in t.start_xy_m.items():
            for tag in start_violations(reg, oid, xy, t.start_xy_m):
                if tag == "dist":
                    pts = [np.asarray(v, float) for v in t.start_xy_m.values()]
                    d = min(
                        float(np.linalg.norm(a - b))
                        for i, a in enumerate(pts)
                        for b in pts[i + 1 :]
                    )
                    msg = (
                        f"{t.trial}: objects {d * 1000:.1f} mm apart, "
                        f"minimum {MIN_CENTRE_DISTANCE_M * 1000:.0f} mm"
                    )
                elif tag == "area":
                    msg = f"{t.trial}: {oid} start {xy} outside the work area"
                elif tag == "tcp":
                    msg = f"{t.trial}: {oid} start {xy} not graspable (TCP outside the work area)"
                else:
                    msg = f"{t.trial}: {oid} start {xy} overlaps {tag.split(':', 1)[1]}"
                if msg not in problems:
                    problems.append(msg)
    # (b) every combination at least twice
    for (o, d), n in combos.items():
        if n < MIN_REPEATS_PER_COMBINATION:
            problems.append(
                f"{o} -> {d} appears {n} time(s), minimum {MIN_REPEATS_PER_COMBINATION}"
            )
    # (c) the target switches every trial
    for a, b in zip(m.trials, m.trials[1:], strict=False):
        if a.target == b.target:
            problems.append(f"{a.trial} and {b.trial} both target {a.target}")
    return problems
