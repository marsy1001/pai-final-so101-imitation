"""Simplified grasp ("hold on close"), as an application rule on top of MuJoCo welds.

Rules (fixed on 2026-09-19):
- On a close command, hold only if exactly one registered object is inside the grasp
  zone between the jaws. Zero or several candidates: close without holding.
- Holding freezes the current relative pose (no snap, no teleport); an unreached
  target is never corrected by the hold.
- An open command releases. Reset / stop-related release is done by the caller
  through `release_all`; nothing is carried across trials.
The hold list is diagnostic and feeds rejection rules; success is judged from the
object pose and the gripper state, never from this list (execution.judge).
"""

from __future__ import annotations

from dataclasses import dataclass

import mujoco
import numpy as np

from ..frames import make_transform
from .model import So101Sim
from .scene import GRIPPER_BODY, weld_name

# Grasp zone in the tip-site frame (metres): X along the approach axis (toward the
# fingertips), Y across the pads, Z along the jaw-opening direction. The site sits on
# the fixed jaw's inner face (measured 2026-09-19: fixed pad at Z -7..0 mm, moving jaw
# at Z +48..55 mm when closed to 30), so the pocket is on the +Z side of the site.
# Single source: the hold rule below uses them at the close command, and the carry audit
# in execution.judge re-checks them at every control tick (fixed 2026-09-19).
# Defined here rather than in execution.criteria: execution.__init__ imports primitives,
# which reaches robot.mujoco_backend, which imports this module - importing the other
# way round would be circular.
GRASP_ZONE_X_M = 0.025
GRASP_ZONE_Y_M = 0.020
GRASP_ZONE_Z_MIN_M = -0.005
GRASP_ZONE_Z_MAX_M = 0.045


@dataclass
class GraspEvent:
    action: str  # "hold" | "no_hold" | "release"
    object_id: str | None
    candidates: tuple[str, ...]
    time_s: float


class SimpleGrasp:
    def __init__(self, sim: So101Sim, object_ids: tuple[str, ...]) -> None:
        self.sim = sim
        self.object_ids = tuple(object_ids)
        self._eq = {
            o: mujoco.mj_name2id(sim.model, mujoco.mjtObj.mjOBJ_EQUALITY, weld_name(o))
            for o in object_ids
        }
        self._body = {
            o: mujoco.mj_name2id(sim.model, mujoco.mjtObj.mjOBJ_BODY, o) for o in object_ids
        }
        self._gripper = mujoco.mj_name2id(sim.model, mujoco.mjtObj.mjOBJ_BODY, GRIPPER_BODY)
        missing = [o for o, e in self._eq.items() if e < 0]
        if missing:
            raise ValueError(f"scene has no weld for {missing}")
        self.held: str | None = None
        self.events: list[GraspEvent] = []

    def candidates_in_zone(self) -> tuple[str, ...]:
        T_tip = self.sim.tip_pose()
        R, t = T_tip[:3, :3], T_tip[:3, 3]
        out = []
        for o, b in self._body.items():
            local = R.T @ (self.sim.data.xpos[b] - t)
            if (
                abs(local[0]) <= GRASP_ZONE_X_M
                and abs(local[1]) <= GRASP_ZONE_Y_M
                and GRASP_ZONE_Z_MIN_M <= local[2] <= GRASP_ZONE_Z_MAX_M
            ):
                out.append(o)
        return tuple(out)

    def on_close(self) -> GraspEvent:
        cands = self.candidates_in_zone()
        if self.held is not None or len(cands) != 1:
            ev = GraspEvent("no_hold", None, cands, self.sim.data.time)
        else:
            self._activate(cands[0])
            ev = GraspEvent("hold", cands[0], cands, self.sim.data.time)
        self.events.append(ev)
        return ev

    def on_open(self) -> GraspEvent:
        released = self.held
        self.release_all()
        ev = GraspEvent("release", released, (), self.sim.data.time)
        self.events.append(ev)
        return ev

    def release_all(self) -> None:
        for eq in self._eq.values():
            self.sim.data.eq_active[eq] = 0
        self.held = None

    def _activate(self, object_id: str) -> None:
        m, d = self.sim.model, self.sim.data
        g, b = self._gripper, self._body[object_id]
        T_g = make_transform(np.array(d.xmat[g]).reshape(3, 3), np.array(d.xpos[g]))
        T_b = make_transform(np.array(d.xmat[b]).reshape(3, 3), np.array(d.xpos[b]))
        T_rel = np.linalg.inv(T_g) @ T_b
        quat = np.empty(4)
        mujoco.mju_mat2Quat(quat, T_rel[:3, :3].reshape(-1))
        eq = self._eq[object_id]
        m.eq_data[eq][0:3] = 0.0  # anchor in body2 frame
        m.eq_data[eq][3:6] = T_rel[:3, 3]  # relpose: body2 in body1 frame
        m.eq_data[eq][6:10] = quat
        m.eq_data[eq][10] = 1.0  # torquescale
        d.eq_active[eq] = 1
        self.held = object_id
