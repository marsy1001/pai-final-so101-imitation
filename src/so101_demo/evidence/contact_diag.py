"""Contact-grasp diagnosis for Issue #4 (Phase 0: one recorded trial, Phase 1: kinematic sweep).

    uv run python -m so101_demo.evidence.contact_diag [--trial T5] [--out DIR]

Nothing about physics or control is changed: the trial is the existing
`sim-v1-contact` run, recorded at every physics step instead of every control
tick, and the sweep only moves the jaw joint in a separate MjData with
mj_fwdPosition (no dynamics, no solver forces).

Phase 0 records, per physics step: phase, gripper qpos / qvel / requested and
effective ctrl / actuator_force / qfrc_actuator / qfrc_constraint / qfrc_passive,
the actual TCP, the cube pose and velocity, and every contact with geom names,
dist, position, normal, efc_address and the raw mj_contactForce plus a world-frame
normal force with the sign normalised toward the cube.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import mujoco
import numpy as np

from ..execution import criteria as C
from ..execution.primitives import GraspParams, PickPlace, TrialTimeout
from ..execution.safety import SafetyLimits, SafetyRejected
from ..execution.trial import HOME_XYZ_M
from ..frames import GRIPPER_JOINT, JOINT_NAMES, MJCF_TIP_SITE
from ..kinematics import So101Kinematics, solve_top_down
from ..locate import SimGroundTruth
from ..plan import load_registry
from ..robot import MujocoBackend
from ..sim.scene import GRIPPER_BODY, SceneConfig, cube_geom_name, load_scene, set_object_pose
from ..units import rad_to_deg

MOVING_JAW_BODY = "moving_jaw_so101_v1"
STEP_COLUMNS = (
    "step",
    "t",
    "phase",
    "jaw_qpos_deg",
    "jaw_qvel_deg_s",
    "jaw_ctrl_requested_deg",
    "jaw_ctrl_effective_deg",
    "actuator_force",
    "qfrc_actuator",
    "qfrc_constraint",
    "qfrc_passive",
    "tcp_x",
    "tcp_y",
    "tcp_z",
    "cube_x",
    "cube_y",
    "cube_z",
    "cube_qw",
    "cube_qx",
    "cube_qy",
    "cube_qz",
    "cube_vx",
    "cube_vy",
    "cube_vz",
    "cube_wx",
    "cube_wy",
    "cube_wz",
    "cube_in_gripper_x",
    "cube_in_gripper_y",
    "cube_in_gripper_z",
    "n_contacts",
    "n_cube_fixed",
    "n_cube_moving",
    "n_cube_table",
    "n_cube_other",
    "n_jaw_table",
    "max_pen_cube_fixed_mm",
    "max_pen_cube_moving_mm",
    "max_pen_cube_table_mm",
    "max_pen_cube_other_mm",
    "fn_cube_fixed",
    "fn_cube_moving",
    "fn_cube_table",
)
CONTACT_COLUMNS = (
    "step",
    "t",
    "phase",
    "geom1",
    "geom2",
    "body1",
    "body2",
    "pair_class",
    "dist_mm",
    "efc_address",
    "dim",
    "pos_x",
    "pos_y",
    "pos_z",
    "normal_x",
    "normal_y",
    "normal_z",
    "raw_f0",
    "raw_f1",
    "raw_f2",
    "raw_t0",
    "raw_t1",
    "raw_t2",
    "fn_on_cube_world_x",
    "fn_on_cube_world_y",
    "fn_on_cube_world_z",
    "fn_magnitude",
    "cube_face",
)


def geom_label(m: mujoco.MjModel, g: int) -> str:
    name = mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_GEOM, g)
    if name:
        return name
    body = mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_BODY, m.geom_bodyid[g])
    if m.geom_type[g] == mujoco.mjtGeom.mjGEOM_MESH:
        mesh = mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_MESH, m.geom_dataid[g])
        return f"{body}/mesh:{mesh}"
    return f"{body}/geom{g}"


@dataclass
class Classifier:
    """Names the contact pairs that matter for a pinch grasp."""

    m: mujoco.MjModel
    cube_geom: int
    fixed_geoms: set[int]
    moving_geoms: set[int]
    table_geom: int
    cube_body: int

    @classmethod
    def build(cls, m: mujoco.MjModel, object_id: str) -> Classifier:
        gb = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, GRIPPER_BODY)
        mb = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, MOVING_JAW_BODY)
        return cls(
            m,
            mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_GEOM, cube_geom_name(object_id)),
            {g for g in range(m.ngeom) if m.geom_bodyid[g] == gb},
            {g for g in range(m.ngeom) if m.geom_bodyid[g] == mb},
            mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_GEOM, "table"),
            mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, object_id),
        )

    def classify(self, g1: int, g2: int) -> str:
        pair = {g1, g2}
        if self.cube_geom in pair:
            other = (pair - {self.cube_geom}).pop() if len(pair) == 2 else self.cube_geom
            if other in self.fixed_geoms:
                return "cube-fixed_jaw"
            if other in self.moving_geoms:
                return "cube-moving_jaw"
            if other == self.table_geom:
                return "cube-table"
            return "cube-other"
        if self.table_geom in pair and (pair & (self.fixed_geoms | self.moving_geoms)):
            return "jaw-table"
        if pair & (self.fixed_geoms | self.moving_geoms):
            return "jaw-other"
        return "other"


def contact_rows(
    m: mujoco.MjModel, d: mujoco.MjData, cls: Classifier, step: int, phase: str, *, forces: bool
) -> list[dict]:
    """One row per contact. `forces=False` for the kinematic sweep (no solver data)."""
    rows = []
    f6 = np.zeros(6)
    R_cube = np.array(d.xmat[cls.cube_body]).reshape(3, 3)
    for i in range(d.ncon):
        c = d.contact[i]
        frame = np.array(c.frame).reshape(3, 3)  # rows: normal, tangent1, tangent2 (geom1 -> geom2)
        normal = frame[0]
        pair_class = cls.classify(c.geom1, c.geom2)
        row = {
            "step": step,
            "t": float(d.time),
            "phase": phase,
            "geom1": geom_label(m, c.geom1),
            "geom2": geom_label(m, c.geom2),
            "body1": mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_BODY, m.geom_bodyid[c.geom1]),
            "body2": mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_BODY, m.geom_bodyid[c.geom2]),
            "pair_class": pair_class,
            "dist_mm": float(c.dist) * 1000,
            "efc_address": int(c.efc_address),
            "dim": int(c.dim),
            "pos_x": float(c.pos[0]),
            "pos_y": float(c.pos[1]),
            "pos_z": float(c.pos[2]),
            "normal_x": float(normal[0]),
            "normal_y": float(normal[1]),
            "normal_z": float(normal[2]),
        }
        # which cube face carries the contact: the face whose outward normal (in the
        # cube's own frame) is anti-parallel to the contact normal pointing into the
        # cube. Using the contact position instead mislabels edge contacts.
        if cls.cube_geom in (c.geom1, c.geom2):
            n_into_cube = normal if c.geom2 == cls.cube_geom else -normal
            outward = R_cube.T @ (-n_into_cube)
            axis = int(np.argmax(np.abs(outward)))
            row["cube_face"] = ("+" if outward[axis] > 0 else "-") + "xyz"[axis]
        else:
            row["cube_face"] = ""
        if forces and c.efc_address >= 0:
            mujoco.mj_contactForce(m, d, i, f6)
            row.update(
                {
                    "raw_f0": f6[0],
                    "raw_f1": f6[1],
                    "raw_f2": f6[2],
                    "raw_t0": f6[3],
                    "raw_t1": f6[4],
                    "raw_t2": f6[5],
                }
            )
            # world-frame normal force on the cube: mj_contactForce gives the force in the
            # contact frame acting on geom2 (normal points geom1 -> geom2); on geom1 it is
            # the opposite. Normalise the sign toward the cube.
            f_world = frame.T @ f6[:3]
            sign = 1.0 if c.geom2 == cls.cube_geom else (-1.0 if c.geom1 == cls.cube_geom else 0.0)
            fn = sign * normal * f6[0]
            row.update(
                {
                    "fn_on_cube_world_x": fn[0],
                    "fn_on_cube_world_y": fn[1],
                    "fn_on_cube_world_z": fn[2],
                    "fn_magnitude": float(abs(f6[0])),
                }
            )
            row["_f_world_total"] = (sign * f_world).tolist()
        else:
            row.update(
                {
                    k: 0.0
                    for k in (
                        "raw_f0",
                        "raw_f1",
                        "raw_f2",
                        "raw_t0",
                        "raw_t1",
                        "raw_t2",
                        "fn_on_cube_world_x",
                        "fn_on_cube_world_y",
                        "fn_on_cube_world_z",
                        "fn_magnitude",
                    )
                }
            )
        rows.append(row)
    return rows


class StepRecordingBackend(MujocoBackend):
    """MujocoBackend that advances one physics step at a time and records each one.
    Control is unchanged: commands still arrive every CONTROL_DT_S from PickPlace."""

    def __init__(self, *a, **kw):
        self.steps: list[dict] = []
        self.contacts: list[dict] = []
        self.phase = "reset"
        self._cls: Classifier | None = None
        self._gt: SimGroundTruth | None = None
        self._oid = ""
        self._requested_ctrl_deg = float("nan")
        super().__init__(*a, **kw)

    def attach(self, gt: SimGroundTruth, object_id: str) -> None:
        self._gt, self._oid = gt, object_id
        self._cls = Classifier.build(self.sim.model, object_id)
        m = self.sim.model
        self._jaw_act = self._act[GRIPPER_JOINT]
        self._jaw_dof = self._dof[GRIPPER_JOINT]
        self._jaw_qpos = self._qpos[GRIPPER_JOINT]
        self._gripper_body = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, GRIPPER_BODY)
        self._cube_dof = int(
            m.jnt_dofadr[mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, f"{object_id}_free")]
        )
        self._cube_qpos = int(
            m.jnt_qposadr[mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, f"{object_id}_free")]
        )

    def command_gripper(self, token, value: float) -> None:
        from .. import robot as _r

        self._requested_ctrl_deg = _r.mujoco_backend.gripper_to_deg(value)
        super().command_gripper(token, value)

    def tick(self, dt_s: float) -> None:
        n = max(1, int(round(dt_s / self.sim.model.opt.timestep)))
        for _ in range(n):
            mujoco.mj_step(self.sim.model, self.sim.data)
            self._record()

    def _record(self) -> None:
        if self._cls is None:
            return
        m, d = self.sim.model, self.sim.data
        step = len(self.steps)
        rows = contact_rows(m, d, self._cls, step, self.phase, forces=True)
        self.contacts.extend(rows)
        cls = self._cls
        q = d.qpos[self._cube_qpos : self._cube_qpos + 7]
        R_g = np.array(d.xmat[self._gripper_body]).reshape(3, 3)
        cube_in_g = R_g.T @ (np.array(d.xpos[cls.cube_body]) - np.array(d.xpos[self._gripper_body]))
        tcp = d.site_xpos[mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_SITE, MJCF_TIP_SITE)]

        def stats(pc):
            sel = [r for r in rows if r["pair_class"] == pc]
            return (
                len(sel),
                (max((-r["dist_mm"] for r in sel), default=0.0)),
                sum(r["fn_magnitude"] for r in sel),
            )

        nf, pf, ff = stats("cube-fixed_jaw")
        nm, pm, fm = stats("cube-moving_jaw")
        nt, pt, ft = stats("cube-table")
        no, po, _ = stats("cube-other")
        njt, _, _ = stats("jaw-table")
        self.steps.append(
            {
                "step": step,
                "t": float(d.time),
                "phase": self.phase,
                "jaw_qpos_deg": float(rad_to_deg(d.qpos[self._jaw_qpos])),
                "jaw_qvel_deg_s": float(rad_to_deg(d.qvel[self._jaw_dof])),
                "jaw_ctrl_requested_deg": self._requested_ctrl_deg,
                "jaw_ctrl_effective_deg": float(rad_to_deg(d.ctrl[self._jaw_act])),
                "actuator_force": float(d.actuator_force[self._jaw_act]),
                "qfrc_actuator": float(d.qfrc_actuator[self._jaw_dof]),
                "qfrc_constraint": float(d.qfrc_constraint[self._jaw_dof]),
                "qfrc_passive": float(d.qfrc_passive[self._jaw_dof]),
                "tcp_x": float(tcp[0]),
                "tcp_y": float(tcp[1]),
                "tcp_z": float(tcp[2]),
                "cube_x": float(q[0]),
                "cube_y": float(q[1]),
                "cube_z": float(q[2]),
                "cube_qw": float(q[3]),
                "cube_qx": float(q[4]),
                "cube_qy": float(q[5]),
                "cube_qz": float(q[6]),
                "cube_vx": float(d.qvel[self._cube_dof]),
                "cube_vy": float(d.qvel[self._cube_dof + 1]),
                "cube_vz": float(d.qvel[self._cube_dof + 2]),
                "cube_wx": float(d.qvel[self._cube_dof + 3]),
                "cube_wy": float(d.qvel[self._cube_dof + 4]),
                "cube_wz": float(d.qvel[self._cube_dof + 5]),
                "cube_in_gripper_x": float(cube_in_g[0]),
                "cube_in_gripper_y": float(cube_in_g[1]),
                "cube_in_gripper_z": float(cube_in_g[2]),
                "n_contacts": len(rows),
                "n_cube_fixed": nf,
                "n_cube_moving": nm,
                "n_cube_table": nt,
                "n_cube_other": no,
                "n_jaw_table": njt,
                "max_pen_cube_fixed_mm": pf,
                "max_pen_cube_moving_mm": pm,
                "max_pen_cube_table_mm": pt,
                "max_pen_cube_other_mm": po,
                "fn_cube_fixed": ff,
                "fn_cube_moving": fm,
                "fn_cube_table": ft,
            }
        )


@dataclass
class Phase0Result:
    trial: str
    verdict_failure: str | None
    steps: list[dict]
    contacts: list[dict]
    trace: list[tuple[float, str]]
    descended_q_deg: np.ndarray | None
    cube_start: np.ndarray
    findings: dict = field(default_factory=dict)


def run_phase0(trial_id: str = "T5") -> Phase0Result:
    reg = load_registry()
    spec = next(t for t in reg.trials_0b if t.trial == trial_id)
    config = SceneConfig("sim-v1-contact", (spec.object,))
    sim = load_scene(config, reg)
    kin = So101Kinematics(sim.arm_limits_rad())
    home_q = solve_top_down(kin, HOME_XYZ_M)
    gt = SimGroundTruth(sim, (spec.object,))
    backend = StepRecordingBackend(sim, None, home_q_deg=home_q)
    backend.attach(gt, spec.object)
    backend.reset()
    start = reg.start_xyz(spec)
    set_object_pose(sim, spec.object, start, reg.objects[spec.object].yaw_deg)
    token = backend.acquire("diag")
    backend.phase = "settle"
    backend.tick(C.RESET_SETTLE_S)
    backend.steps.clear()
    backend.contacts.clear()

    pp = PickPlace(
        backend,
        kin,
        gt,
        reg,
        SafetyLimits(sim.arm_limits_rad()),
        token,
        grasp=GraspParams.for_config("sim-v1-contact"),
        deadline_s=sim.data.time + C.TIMEOUT_S,
    )
    orig_mark = pp._mark

    def mark(label: str) -> None:
        orig_mark(label)
        backend.phase = label

    pp._mark = mark  # type: ignore[method-assign]
    failure = None
    try:
        # pick only: descent -> close -> lift is where the grasp fails; place is not needed
        backend.phase = "pick:approach"
        pp.pick(spec.object)
        backend.phase = "after_lift_hold"
        backend.tick(0.5)
    except (SafetyRejected, TrialTimeout) as e:
        failure = str(e)
    backend.release(token)
    res = Phase0Result(trial_id, failure, backend.steps, backend.contacts, pp.trace, None, start)
    res.descended_q_deg = _arm_q_at(backend, pp.trace, "pick:descended")
    res.findings = analyse_phase0(res)
    return res


def _arm_q_at(backend: StepRecordingBackend, trace, label: str):
    # The arm joints are commanded, not recorded per step; the descended posture is the
    # commanded target at that milestone (PickPlace holds _q_cmd), recovered from the
    # backend command log.
    t_label = next((t for t, lb in trace if lb == label), None)
    if t_label is None:
        return None
    q = None
    for t, kind, payload in backend.command_log:
        if kind == "joints" and t <= t_label + 1e-9:
            q = np.array(payload)
    return q


def _phase_of(steps: list[dict], step: int) -> str:
    return steps[step]["phase"]


def analyse_phase0(res: Phase0Result) -> dict:
    """First anomaly per criterion, with the phase it happened in."""
    S = res.steps
    start = res.cube_start
    f: dict = {}

    def first(pred, key):
        for s in S:
            if pred(s):
                f[key] = {"step": s["step"], "t": s["t"], "phase": s["phase"]}
                return s
        f[key] = None
        return None

    def cube_disp_mm(s):
        return (
            float(np.linalg.norm(np.array([s["cube_x"], s["cube_y"], s["cube_z"]]) - start)) * 1000
        )

    def cube_tilt_deg(s):
        qx, qy = s["cube_qx"], s["cube_qy"]
        zz = 1 - 2 * (qx * qx + qy * qy)
        return float(np.degrees(np.arccos(np.clip(zz, -1, 1))))

    first(lambda s: cube_disp_mm(s) > 1.0, "cube_moved_gt_1mm")
    first(lambda s: cube_tilt_deg(s) > 1.0, "cube_tilted_gt_1deg")
    first(lambda s: s["max_pen_cube_table_mm"] > 1.0, "cube_table_pen_gt_1mm")
    first(lambda s: s["max_pen_cube_fixed_mm"] > 3.0, "cube_fixed_pen_gt_3mm")
    first(lambda s: s["max_pen_cube_moving_mm"] > 3.0, "cube_moving_pen_gt_3mm")
    first(lambda s: s["n_cube_other"] > 0, "cube_other_contact")
    first(lambda s: s["n_jaw_table"] > 0, "jaw_table_contact")
    first(lambda s: s["n_cube_fixed"] > 0, "first_cube_fixed_contact")
    first(lambda s: s["n_cube_moving"] > 0, "first_cube_moving_contact")
    first(lambda s: abs(s["actuator_force"]) >= 3.35 - 1e-6, "actuator_saturated")
    first(lambda s: s["n_cube_fixed"] > 0 and s["n_cube_moving"] > 0, "bilateral_contact")
    # per-phase summary
    phases = {}
    for s in S:
        p = phases.setdefault(
            s["phase"],
            {
                "t0": s["t"],
                "t1": s["t"],
                "max_pen_fixed": 0.0,
                "max_pen_moving": 0.0,
                "max_pen_table": 0.0,
                "max_disp_mm": 0.0,
                "max_tilt_deg": 0.0,
                "max_actuator_force": 0.0,
                "min_jaw_deg": 1e9,
                "max_fn_fixed": 0.0,
                "max_fn_moving": 0.0,
            },
        )
        p["t1"] = s["t"]
        p["max_pen_fixed"] = max(p["max_pen_fixed"], s["max_pen_cube_fixed_mm"])
        p["max_pen_moving"] = max(p["max_pen_moving"], s["max_pen_cube_moving_mm"])
        p["max_pen_table"] = max(p["max_pen_table"], s["max_pen_cube_table_mm"])
        p["max_disp_mm"] = max(p["max_disp_mm"], cube_disp_mm(s))
        p["max_tilt_deg"] = max(p["max_tilt_deg"], cube_tilt_deg(s))
        p["max_actuator_force"] = max(p["max_actuator_force"], abs(s["actuator_force"]))
        p["min_jaw_deg"] = min(p["min_jaw_deg"], s["jaw_qpos_deg"])
        p["max_fn_fixed"] = max(p["max_fn_fixed"], s["fn_cube_fixed"])
        p["max_fn_moving"] = max(p["max_fn_moving"], s["fn_cube_moving"])
    f["phases"] = phases
    # ctrl overwrite check during lift: effective gripper ctrl must stay at the close command
    lift = [s for s in S if s["phase"] in ("pick:closed",)]
    if lift:
        vals = {round(s["jaw_ctrl_effective_deg"], 3) for s in lift}
        f["gripper_ctrl_constant_during_lift"] = len(vals) == 1
        f["gripper_ctrl_values_during_lift_deg"] = sorted(vals)[:5]
    # does the TCP actually rise during lift?
    if lift:
        f["tcp_z_at_lift_start_mm"] = lift[0]["tcp_z"] * 1000
        f["tcp_z_at_lift_end_mm"] = lift[-1]["tcp_z"] * 1000
        f["cube_z_at_lift_end_mm"] = lift[-1]["cube_z"] * 1000
    # contact faces seen for the jaw pairs
    faces = {}
    for r in res.contacts:
        if r["pair_class"] in ("cube-fixed_jaw", "cube-moving_jaw"):
            faces.setdefault(r["pair_class"], {}).setdefault(r["cube_face"], 0)
            faces[r["pair_class"]][r["cube_face"]] += 1
    f["cube_faces_by_pair"] = faces
    f["closing_direction"] = "smaller q closes (ctrl -10 deg = closed, +100 deg = open)"
    return f


# ---------------------------------------------------------------------------------
# Phase 1: kinematic sweep


@dataclass
class SweepResult:
    arm_q_deg: np.ndarray
    cube_start: np.ndarray
    pre_close_interference: list[dict]
    coarse: list[dict]  # per angle: n contacts per class, min dist per class
    first_contact: dict  # class -> {angle_deg, dist_mm, faces, normal_vs_closing_deg, refined}
    gap_at_first_contact_mm: float | None
    closing_axis_world: list[float]


def kinematic_sweep(
    arm_q_deg,
    cube_start_xyz,
    object_id: str = "red_cube",
    coarse_step_deg: float = 0.25,
    fine_step_deg: float = 0.05,
) -> SweepResult:
    reg = load_registry()
    sim = load_scene(SceneConfig("sim-v1-contact", (object_id,)), reg)
    m = sim.model
    d = mujoco.MjData(m)  # separate data: nothing from the dynamic run
    cls = Classifier.build(m, object_id)
    jaw_qpos = int(m.jnt_qposadr[mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, GRIPPER_JOINT)])
    for name, v in zip(JOINT_NAMES, np.deg2rad(arm_q_deg), strict=True):
        d.qpos[int(m.jnt_qposadr[mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, name)])] = v
    cq = int(m.jnt_qposadr[mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, f"{object_id}_free")])
    d.qpos[cq : cq + 3] = cube_start_xyz
    d.qpos[cq + 3 : cq + 7] = [1, 0, 0, 0]
    site = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_SITE, MJCF_TIP_SITE)

    def at(angle_deg: float):
        d.qpos[jaw_qpos] = np.radians(angle_deg)
        mujoco.mj_fwdPosition(m, d)
        return contact_rows(m, d, cls, 0, f"sweep@{angle_deg:.2f}", forces=False)

    rows100 = at(100.0)
    closing_axis = -np.array(d.site_xmat[site]).reshape(3, 3)[
        :, 2
    ]  # moving jaw closes toward -Z of the tip site
    pre = [r for r in rows100 if r["dist_mm"] < 0 and r["pair_class"] not in ("other", "jaw-other")]
    coarse = []
    angles = np.arange(100.0, -10.0 - 1e-9, -coarse_step_deg)
    first: dict = {}
    for a in angles:
        rows = at(float(a))
        summary = {"angle_deg": float(a)}
        for pc in ("cube-fixed_jaw", "cube-moving_jaw", "cube-table", "cube-other", "jaw-table"):
            sel = [r for r in rows if r["pair_class"] == pc and r["dist_mm"] <= 0.0]
            summary[f"n_{pc}"] = len(sel)
            summary[f"min_dist_{pc}_mm"] = min((r["dist_mm"] for r in sel), default=float("nan"))
            if sel and pc not in first:
                first[pc] = {"angle_deg": float(a), "rows": sel}
        coarse.append(summary)
    # refine the moving-jaw first contact (the fixed jaw may already touch at 100 deg)
    refined = {}
    for pc, info in first.items():
        a0 = info["angle_deg"]
        if a0 >= 100.0 - 1e-9:
            refined[pc] = {"angle_deg": a0, "note": "already in contact at full open"}
            continue
        hi, lo = a0 + coarse_step_deg, a0  # no contact at hi, contact at lo
        for a in np.arange(hi, lo - 1e-9, -fine_step_deg):
            rows = [r for r in at(float(a)) if r["pair_class"] == pc and r["dist_mm"] <= 0.0]
            if rows:
                normals = np.array([[r["normal_x"], r["normal_y"], r["normal_z"]] for r in rows])
                ang = [
                    float(np.degrees(np.arccos(np.clip(abs(n @ closing_axis), -1, 1))))
                    for n in normals
                ]
                refined[pc] = {
                    "angle_deg": float(a),
                    "dist_mm": min(r["dist_mm"] for r in rows),
                    "faces": sorted({r["cube_face"] for r in rows}),
                    "geoms": sorted({r["geom1"] + "|" + r["geom2"] for r in rows}),
                    "normal_vs_closing_axis_deg": ang,
                }
                break
    gap = None
    if "cube-moving_jaw" in refined and "angle_deg" in refined["cube-moving_jaw"]:
        # effective gap = cube size when the moving jaw first touches while the fixed jaw touches the far face
        gap = reg.objects[object_id].size_m * 1000 + (
            refined["cube-moving_jaw"].get("dist_mm") or 0.0
        )
    return SweepResult(
        np.asarray(arm_q_deg),
        np.asarray(cube_start_xyz),
        pre,
        coarse,
        refined,
        gap,
        closing_axis.tolist(),
    )


def fixed_jaw_hull_over_cube(sim, cls: Classifier, cube_start_xyz, size_m: float) -> dict:
    """Where the fixed-jaw collision hulls sit relative to the cube footprint (kinematic).
    Returns, per fixed-jaw collision mesh, the lowest hull vertex above the cube's xy
    footprint (metres above the table) and how far it reaches below the cube's top."""
    m, d = sim.model, sim.data
    out = {}
    half = size_m / 2
    cx, cy, _ = cube_start_xyz
    for g in cls.fixed_geoms:
        if m.geom_type[g] != mujoco.mjtGeom.mjGEOM_MESH or not (
            m.geom_contype[g] or m.geom_conaffinity[g]
        ):
            continue
        mid = m.geom_dataid[g]
        V = m.mesh_vert[m.mesh_vertadr[mid] : m.mesh_vertadr[mid] + m.mesh_vertnum[mid]]
        W = V @ np.array(d.geom_xmat[g]).reshape(3, 3).T + np.array(d.geom_xpos[g])
        over = W[(np.abs(W[:, 0] - cx) <= half) & (np.abs(W[:, 1] - cy) <= half)]
        name = geom_label(m, g)
        if len(over):
            zmin = float(over[:, 2].min())
            out[name] = {
                "lowest_vertex_over_cube_footprint_mm": zmin * 1000,
                "reach_below_cube_top_mm": (size_m - zmin) * 1000,
                "n_vertices_over_footprint": int(len(over)),
            }
        else:
            out[name] = {
                "lowest_vertex_over_cube_footprint_mm": None,
                "reach_below_cube_top_mm": None,
                "n_vertices_over_footprint": 0,
            }
    return out


def sweep_tcp_heights(
    cube_start_xyz, object_id: str = "red_cube", heights_m=(0.020, 0.025, 0.030, 0.035, 0.040)
) -> list[dict]:
    """Kinematic only: for grasp TCP heights above the table, does the fixed-jaw hull
    still overlap the cube before closing, and where does the moving jaw first touch?
    Diagnostic for the grasp-pose branch; nothing is changed in the trial code."""
    from ..execution.primitives import grasp_tcp_for

    reg = load_registry()
    sim = load_scene(SceneConfig("sim-v1-contact", (object_id,)), reg)
    kin = So101Kinematics(sim.arm_limits_rad())
    size = reg.objects[object_id].size_m
    cls = Classifier.build(sim.model, object_id)
    rows = []
    for h in heights_m:
        centre = np.array([cube_start_xyz[0], cube_start_xyz[1], h])
        try:
            q = solve_top_down(kin, grasp_tcp_for(centre, size, 0.0))
        except Exception as e:  # noqa: BLE001 - reported, not hidden
            rows.append({"tcp_z_mm": h * 1000, "error": str(e)[:60]})
            continue
        sw = kinematic_sweep(q, cube_start_xyz, object_id, coarse_step_deg=0.5, fine_step_deg=0.1)
        sim.set_arm_joints_deg(q)
        hull = fixed_jaw_hull_over_cube(sim, cls, cube_start_xyz, size)
        worst = max((v["reach_below_cube_top_mm"] or 0.0) for v in hull.values()) if hull else 0.0
        pre = [r for r in sw.pre_close_interference if r["pair_class"] != "cube-table"]
        mv = sw.first_contact.get("cube-moving_jaw", {})
        rows.append(
            {
                "tcp_z_mm": h * 1000,
                "fingertip_z_mm": (h - 0.0063) * 1000,
                "fixed_hull_reach_below_cube_top_mm": worst,
                "pre_close_interference": "; ".join(
                    f"{r['pair_class']} {r['cube_face']} {r['dist_mm']:.2f}mm" for r in pre
                )
                or "none",
                "moving_jaw_first_contact_deg": mv.get("angle_deg"),
                "moving_jaw_faces": ",".join(mv.get("faces", [])),
                "moving_jaw_normal_vs_axis_deg": ",".join(
                    f"{a:.0f}" for a in mv.get("normal_vs_closing_axis_deg", [])
                ),
            }
        )
    return rows


# ---------------------------------------------------------------------------------


def _write_csv_gz(path: Path, rows: list[dict], columns) -> None:
    with gzip.open(path, "wt", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(columns), extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def render(
    res: Phase0Result,
    sweep: SweepResult | None,
    today: str,
    heights: list[dict] | None = None,
    hull: dict | None = None,
) -> str:
    f = res.findings

    def ev(key):
        v = f.get(key)
        return "なし" if v is None else f"step {v['step']}（t={v['t']:.3f} s、{v['phase']}）"

    phase_rows = [
        f"| {p} | {v['t0']:.2f}–{v['t1']:.2f} | {v['min_jaw_deg']:.1f} | {v['max_actuator_force']:.2f} | {v['max_pen_fixed']:.2f} | {v['max_pen_moving']:.2f} | {v['max_pen_table']:.2f} | {v['max_disp_mm']:.2f} | {v['max_tilt_deg']:.2f} | {v['max_fn_fixed']:.2f} | {v['max_fn_moving']:.2f} |"
        for p, v in f["phases"].items()
    ]
    faces = (
        "; ".join(
            f"{k}: " + ", ".join(f"{face}×{n}" for face, n in sorted(v.items()))
            for k, v in f["cube_faces_by_pair"].items()
        )
        or "なし"
    )
    sweep_md = "（実施せず）"
    heights_md = ""
    if heights:
        hr = []
        for r in heights:
            if "error" in r:
                hr.append(f"| {r['tcp_z_mm']:.0f} | IK 拒否: {r['error']} | | | | |")
            else:
                hr.append(
                    f"| {r['tcp_z_mm']:.0f} | {r['fingertip_z_mm']:.1f} | {r['fixed_hull_reach_below_cube_top_mm']:.2f} | {r['pre_close_interference']} | {r['moving_jaw_first_contact_deg']} | {r['moving_jaw_faces']} ({r['moving_jaw_normal_vs_axis_deg']}°) |"
                )
        pens = [
            (r["tcp_z_mm"], -float(r["pre_close_interference"].split("mm")[0].split()[-1]))
            for r in heights
            if "error" not in r and "cube-fixed_jaw" in r["pre_close_interference"]
        ]
        tilt_md = ""
        if len(pens) >= 2:
            (z0, p0), (z1, p1) = pens[0], pens[-1]
            slope = (p0 - p1) / (z1 - z0)
            tilt_md = (
                f"固定顎凸包の全開時干渉は TCP 高さ {z0:.0f} → {z1:.0f} mm で {p0:.2f} → {p1:.2f} mm と直線的に減る。"
                f"凸包の内面は鉛直から約 **{np.degrees(np.arctan(slope)):.0f}°** 傾いている。"
                "（この段落の「指先側ほど張り出す」という当初の向きの解釈は、2026-09-19 の設計文書 "
                "`docs/design/contact-grasp-approach.md`「原因の再検証」で断面計算により逆（根元側ほど張り出す）と訂正された。"
                "数値はそのまま有効）"
            )
        heights_md = f"""
### 把持点の高さを変えた場合（走査のみ、立方体は初期 pose、隙間 0）
{tilt_md}
| TCP z mm | 指先 z mm | 固定顎凸包が立方体上面より下に届く量 mm | 全開時の干渉（机以外） | 可動顎 初接触 ° | 可動顎の接触面（法線角） |
|---|---|---|---|---|---|
{chr(10).join(hr)}
"""
    if sweep is not None:
        pre = (
            "なし"
            if not sweep.pre_close_interference
            else "; ".join(
                f"{r['pair_class']} {r['geom1']}|{r['geom2']} dist {r['dist_mm']:.2f} mm face {r['cube_face']}"
                for r in sweep.pre_close_interference
            )
        )
        fc = []
        for pc, info in sweep.first_contact.items():
            if "note" in info:
                fc.append(f"| {pc} | {info['angle_deg']:.2f} | {info['note']} | | |")
            else:
                fc.append(
                    f"| {pc} | {info['angle_deg']:.2f} | {info['dist_mm']:.3f} | {', '.join(info['faces'])} | {', '.join(f'{a:.1f}' for a in info['normal_vs_closing_axis_deg'])} |"
                )
        sweep_md = (
            f"""- 走査条件: 別 `MjData`、アーム関節 = 降下完了時の指令姿勢 {np.round(sweep.arm_q_deg, 2).tolist()} deg、立方体 = 初期 pose {np.round(sweep.cube_start, 4).tolist()} m（yaw 0）、顎角度 100° → −10°、刻み 0.25°、初接触近傍 0.05°、`mj_fwdPosition` のみ。接触力は評価しない
- 閉じ方向（world）: {np.round(sweep.closing_axis_world, 3).tolist()}（tip site の −Z。可動顎はこの向きに閉じる）
- 全開（100°）での干渉（dist < 0）: {pre}

| ペア | 初接触 角度 ° | dist mm | 立方体の面 | 法線と閉じ方向の角 ° |
|---|---|---|---|---|
{chr(10).join(fc)}

- 初接触時の実効間隔（立方体 40 mm + dist）: {"-" if sweep.gap_at_first_contact_mm is None else f"{sweep.gap_at_first_contact_mm:.2f} mm"}
- 降下完了姿勢での固定顎凸包と立方体の footprint: {hull}
"""
            + heights_md
        )
    return f"""# 0b: 接触把持の診断（Phase 0: 記録付き 1 試行、Phase 1: 形状走査）

- 記録日: {today}。生成: `uv run python -m so101_demo.evidence.contact_diag --trial {res.trial}`（再現可能）。物理設定・制御は変更していない
- 対象: 構成 `sim-v1-contact`、trial {res.trial}（`contracts/registry.json`）、pick のみ（降下 → 閉鎖 → 持ち上げ → 0.5 s 保持）。判定器は使わず、pick の結果は {res.verdict_failure or "例外なし"}
- コンパイル後の値: gripper actuator gear 1、kp 998.22、kv 2.731、forcerange ±3.35 N·m、ctrlrange [−10°, 100°]。joint damping 0.6、frictionloss 0.052、armature 0.028。timestep 0.002 s。閉じ方向 = 角度が小さい方（−10° 全閉、100° 全開）
- 記録: 物理 step ごと（`contact-diagnosis-{res.trial.lower()}-steps.csv.gz`、接触ごとは `-contacts.csv.gz`）。`mj_contactForce` は raw を保存し、world 系の法線力は `frame.T @ f` を立方体側の符号に正規化した派生列

## 結論: 最初に異常が出る段階
| 事象 | 最初の発生 |
|---|---|
| 立方体が初期位置から 1 mm 超動く | {ev("cube_moved_gt_1mm")} |
| 立方体の傾き 1° 超 | {ev("cube_tilted_gt_1deg")} |
| 立方体–机の貫入 1 mm 超 | {ev("cube_table_pen_gt_1mm")} |
| 立方体–固定顎の貫入 3 mm 超 | {ev("cube_fixed_pen_gt_3mm")} |
| 立方体–可動顎の貫入 3 mm 超 | {ev("cube_moving_pen_gt_3mm")} |
| 立方体と顎以外（筐体・手首）の接触 | {ev("cube_other_contact")} |
| 顎–机の接触 | {ev("jaw_table_contact")} |
| 立方体–固定顎の最初の接触 | {ev("first_cube_fixed_contact")} |
| 立方体–可動顎の最初の接触 | {ev("first_cube_moving_contact")} |
| 両顎が同時に接触 | {ev("bilateral_contact")} |
| actuator が力上限に張り付く | {ev("actuator_saturated")} |

## 段階別の最大値
| 段階 | 時間 s | 顎角 最小 ° | \\|actuator_force\\| 最大 N·m | 貫入 固定顎 mm | 貫入 可動顎 mm | 貫入 机 mm | 立方体 変位 mm | 傾き ° | 法線力 固定顎 N | 法線力 可動顎 N |
|---|---|---|---|---|---|---|---|---|---|---|
{chr(10).join(phase_rows)}

- 接触が載っている立方体の面（ペア別）: {faces}
- 持ち上げ中の gripper ctrl: {"一定（上書きなし）" if f.get("gripper_ctrl_constant_during_lift") else "変化あり（要確認）"} {f.get("gripper_ctrl_values_during_lift_deg", "")}
- 持ち上げ: TCP z {f.get("tcp_z_at_lift_start_mm", float("nan")):.1f} → {f.get("tcp_z_at_lift_end_mm", float("nan")):.1f} mm、立方体 z 終了時 {f.get("cube_z_at_lift_end_mm", float("nan")):.1f} mm

## Phase 1: 形状走査
{sweep_md}
## 参照
- `src/so101_demo/evidence/contact_diag.py`、`tests/test_contact_diag.py`
- MuJoCo contact frame / mj_contactForce: https://mujoco.readthedocs.io/en/3.13.0/programming/simulation.html#contacts
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--trial", default="T5")
    ap.add_argument("--out", type=Path, default=Path("docs/evidence/0b"))
    ap.add_argument("--no-sweep", action="store_true")
    args = ap.parse_args(argv)
    today = dt.date.today().isoformat()
    res = run_phase0(args.trial)
    sweep = None
    heights = None
    hull = None
    if not args.no_sweep and res.descended_q_deg is not None:
        sweep = kinematic_sweep(res.descended_q_deg, res.cube_start)
        sim = load_scene(SceneConfig("sim-v1-contact", ("red_cube",)))
        sim.set_arm_joints_deg(res.descended_q_deg)
        hull = fixed_jaw_hull_over_cube(
            sim, Classifier.build(sim.model, "red_cube"), res.cube_start, 0.04
        )
        heights = sweep_tcp_heights(res.cube_start)
    args.out.mkdir(parents=True, exist_ok=True)
    stem = f"contact-diagnosis-{args.trial.lower()}"
    _write_csv_gz(args.out / f"{stem}-steps.csv.gz", res.steps, STEP_COLUMNS)
    _write_csv_gz(args.out / f"{stem}-contacts.csv.gz", res.contacts, CONTACT_COLUMNS)
    (args.out / "contact-diagnosis.md").write_text(
        render(res, sweep, today, heights, hull), encoding="utf-8"
    )
    summary = {
        "trial": args.trial,
        "findings": res.findings,
        "sweep": None
        if sweep is None
        else {
            "first_contact": sweep.first_contact,
            "pre_close_interference": sweep.pre_close_interference,
            "gap_at_first_contact_mm": sweep.gap_at_first_contact_mm,
            "arm_q_deg": sweep.arm_q_deg.tolist(),
        },
        "fixed_jaw_hull": hull,
        "tcp_heights": heights,
    }
    (args.out / f"{stem}.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=float), encoding="utf-8"
    )
    print(
        f"wrote {args.out / 'contact-diagnosis.md'} ({len(res.steps)} steps, {len(res.contacts)} contact rows)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
