"""Run one fixed 0b trial headless and judge it. Records every sample for the log.

Both configurations run here: `sim-v1-simple` (welds under the simplified-grasp
rules) and `sim-v1-contact` (no welds; the jaws squeeze the cube and friction
carries it). For the contact configuration the run also records the numeric
diagnostics a contact problem is judged from without rendering: contacts between
the jaws and the cube, their maximum penetration, and how far the cube slid in
the gripper frame while carried.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

import mujoco
import numpy as np

from ..kinematics import So101Kinematics, solve_top_down
from ..locate import SimGroundTruth
from ..plan import Registry, TrialSpec, load_registry
from ..robot import MujocoBackend
from ..sim.grasp import SimpleGrasp
from ..sim.model import So101Sim
from ..sim.scene import (
    GRIPPER_BODY,
    SceneConfig,
    cube_geom_name,
    load_scene,
    penetrations,
    set_object_pose,
)
from . import criteria as C
from .judge import Sample, Verdict, judge
from .primitives import CONTROL_DT_S, GraspParams, PickPlace, TrialTimeout
from .safety import SafetyLimits, SafetyRejected

HOME_XYZ_M = (0.19, 0.0, 0.07)  # TCP above the work-area centre; joints solved once per model


@dataclass
class TrialResult:
    spec: TrialSpec
    config_id: str
    verdict: Verdict
    samples: list[Sample]
    sim_time_s: float
    wall_time_s: float
    home_q_deg: np.ndarray
    initial_penetrations: list
    diagnostics: dict = field(default_factory=dict)
    video_path: str | None = None  # set by evidence.stage1_0b when it records
    video_bytes: int | None = None


class RecordingBackend(MujocoBackend):
    """MujocoBackend that samples the object (and jaw-cube contacts) after every tick."""

    def __init__(self, *a, **kw):
        self.samples: list[Sample] = []
        self.contact_log: list[
            tuple[float, int, float, np.ndarray]
        ] = []  # t, n contacts, max depth, cube in gripper
        self.on_tick: Callable[[So101Sim], None] | None = None  # e.g. a video recorder
        self._gt: SimGroundTruth | None = None
        self._oid = ""
        self._cube_geom = -1
        self._jaw_geoms: set[int] = set()
        super().__init__(*a, **kw)

    def attach(self, gt: SimGroundTruth, object_id: str) -> None:
        m = self.sim.model
        self._gt, self._oid = gt, object_id
        self._cube_geom = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_GEOM, cube_geom_name(object_id))
        jaw_bodies = {
            mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, GRIPPER_BODY),
            mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, "moving_jaw_so101_v1"),
        }
        self._jaw_geoms = {g for g in range(m.ngeom) if m.geom_bodyid[g] in jaw_bodies}
        self._gripper_body = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, GRIPPER_BODY)

    def tick(self, dt_s: float) -> None:
        # long waits (reset settle, post-release window) are stepped in control-period
        # chunks so that the samples and the video keep their 50 Hz / 25 fps cadence
        n = max(1, int(round(dt_s / CONTROL_DT_S)))
        for _ in range(n):
            super().tick(dt_s / n)
            self._observe()

    def _observe(self) -> None:
        if self.on_tick is not None:
            self.on_tick(self.sim)
        if self._gt is None:
            return
        p = self._gt.pose_of(self._oid)
        # audit inputs (Issue #4 固定 3/3): where the object sits in the tip-site frame,
        # and how many equality constraints are active. Both are read from the model,
        # not from the grasp hold list.
        tip = self.sim.tip_pose()
        self.samples.append(
            Sample(
                p.time_s,
                p.position_m,
                p.rotation,
                p.linear_velocity,
                p.angular_velocity,
                self.state().gripper,
                tip[:3, 3],
                obj_in_tip=tip[:3, :3].T @ (p.position_m - tip[:3, 3]),
                constraints_active=int(np.count_nonzero(self.sim.data.eq_active)),
            )
        )
        d = self.sim.data
        n, depth = 0, 0.0
        for i in range(d.ncon):
            c = d.contact[i]
            pair = {c.geom1, c.geom2}
            if self._cube_geom in pair and pair & self._jaw_geoms:
                n += 1
                depth = max(depth, float(-c.dist))
        R_g = np.array(d.xmat[self._gripper_body]).reshape(3, 3)
        cube_in_gripper = R_g.T @ (p.position_m - np.array(d.xpos[self._gripper_body]))
        self.contact_log.append((p.time_s, n, depth, cube_in_gripper))


def _diagnostics(
    backend: RecordingBackend, trace: list[tuple[float, str]], verdict: Verdict
) -> dict:
    t_of = {label: t for t, label in trace}
    out: dict = {}
    closed_t, released_t = t_of.get("pick:closed"), t_of.get("place:released")
    if closed_t is not None:
        at = min(backend.contact_log, key=lambda r: abs(r[0] - closed_t))
        out["contacts_at_close"] = at[1]
        out["max_penetration_at_close_mm"] = at[2] * 1000
        out["jaw_value_at_close"] = next(s.gripper for s in backend.samples if s.t >= closed_t)
    if verdict.lift_time is not None:
        end = released_t if released_t is not None else backend.contact_log[-1][0]
        held = [r for r in backend.contact_log if verdict.lift_time <= r[0] <= end]
        if held:
            ref = held[0][3]
            out["slip_while_carried_mm"] = (
                max(float(np.linalg.norm(r[3] - ref)) for r in held) * 1000
            )
            out["min_contacts_while_carried"] = min(r[1] for r in held)
            out["max_penetration_while_carried_mm"] = max(r[2] for r in held) * 1000
    return out


def run_trial(
    spec: TrialSpec,
    config_id: str = "sim-v1-simple",
    registry: Registry | None = None,
    *,
    observer: Callable[[So101Sim], Callable[[So101Sim], None]] | None = None,
    objects: tuple[str, ...] | None = None,
) -> TrialResult:
    """Run and judge one trial. `observer(sim)` (optional) is called once with the
    loaded scene and returns a per-tick callback, e.g. a video recorder; the trial
    itself does not know about rendering. `objects` (optional) puts more registered
    objects in the scene than the trial's own; they start at their registered home and
    their final displacement from it is recorded in `diagnostics` (Issue #21 sweep).
    The 0b manifest itself runs with the trial object alone."""
    reg = registry or load_registry()
    scene_objects = tuple(objects) if objects else (spec.object,)
    if spec.object not in scene_objects:
        raise ValueError(f"{spec.object} is not among the scene objects {scene_objects}")
    config = SceneConfig(config_id, scene_objects)
    sim = load_scene(config, reg)
    kin = So101Kinematics(sim.arm_limits_rad())
    home_q = solve_top_down(kin, HOME_XYZ_M)
    grasp = SimpleGrasp(sim, scene_objects) if config.simplified_grasp else None
    gt = SimGroundTruth(sim, scene_objects)
    backend = RecordingBackend(sim, grasp, home_q_deg=home_q)
    backend.attach(gt, spec.object)
    if observer is not None:
        backend.on_tick = observer(sim)
    wall0 = time.perf_counter()

    # reset: home posture, object at its start pose, settle
    backend.reset()
    other_objects = [o for o in scene_objects if o != spec.object]
    for o in other_objects:
        set_object_pose(sim, o, reg.objects[o].home_xyz_m, reg.objects[o].yaw_deg)
    set_object_pose(sim, spec.object, reg.start_xyz(spec), reg.objects[spec.object].yaw_deg)
    pen = penetrations(sim, min_depth_m=1e-3)
    token = backend.acquire("trial")
    backend.tick(C.RESET_SETTLE_S)
    backend.samples.clear()
    backend.contact_log.clear()

    pick_t = sim.data.time
    deadline = pick_t + C.TIMEOUT_S
    pp = PickPlace(
        backend,
        kin,
        gt,
        reg,
        SafetyLimits(sim.arm_limits_rad()),
        token,
        grasp=GraspParams.for_config(config_id),
        deadline_s=deadline,
    )
    rejected = None
    release_t = None
    try:
        pp.pick(spec.object)
        pp.place(spec.destination, spec.object)
        release_t = next(t for t, label in pp.trace if label == "place:released")
        # keep sampling through the settle window, never past the deadline
        backend.tick(
            min(C.SETTLE_WINDOW_S + C.SETTLE_HOLD_S, max(deadline - sim.data.time, CONTROL_DT_S))
        )
    except SafetyRejected as e:
        rejected = str(e)
    except TrialTimeout:
        pass  # the judge classifies it from the samples (deadline reached, no settle)
    backend.release(token)

    dest = reg.destinations[spec.destination]
    others = [d for k, d in reg.destinations.items() if k != spec.destination]
    verdict = judge(
        backend.samples,
        reg.objects[spec.object],
        dest,
        others,
        pick_cmd_t=pick_t,
        release_t=release_t,
        rejected=rejected,
    )
    diag = _diagnostics(backend, pp.trace, verdict)
    diag["welds_in_model"] = int(sim.model.neq)
    if other_objects:
        diag["other_objects_final_displacement_m"] = {
            o: float(
                np.linalg.norm(gt.pose_of(o).position_m - np.asarray(reg.objects[o].home_xyz_m))
            )
            for o in other_objects
        }
    return TrialResult(
        spec,
        config_id,
        verdict,
        backend.samples,
        sim.data.time - pick_t,
        time.perf_counter() - wall0,
        home_q,
        pen,
        diag,
    )


def control_dt() -> float:
    return CONTROL_DT_S
