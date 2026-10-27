"""RobotBackend on the MuJoCo scene. Wraps sim + the grasp rules; computes no IK."""

from __future__ import annotations

import mujoco
import numpy as np

from ..frames import GRIPPER_JOINT, JOINT_NAMES
from ..sim.grasp import SimpleGrasp
from ..sim.model import So101Sim
from ..units import deg_to_rad, rad_to_deg
from .backend import GRIPPER_MAX, GRIPPER_MIN, CommandToken, NotCommandOwner, RobotState

# Gripper 0-100 <-> MJCF `gripper` hinge angle, linear between the measured endpoints
# (2026-09-19: -10 deg = jaws 1.9 mm apart, +100 deg = fully open). Real robot: stage 4
# re-calibrates this mapping.
GRIPPER_CLOSED_DEG = -10.0
GRIPPER_OPEN_DEG = 100.0
# Commands at or below CLOSE_CMD_MAX are "close" commands, at or above OPEN_CMD_MIN "open"
# (Issue #4 fixed values: closed <= 40, open >= 60). In between: move only.
CLOSE_CMD_MAX = 40.0
OPEN_CMD_MIN = 60.0
MOVING_SPEED_DEG_S = 2.0


def gripper_to_deg(value: float) -> float:
    v = float(np.clip(value, GRIPPER_MIN, GRIPPER_MAX))
    return GRIPPER_CLOSED_DEG + (GRIPPER_OPEN_DEG - GRIPPER_CLOSED_DEG) * v / 100.0


def deg_to_gripper(angle_deg: float) -> float:
    return float((angle_deg - GRIPPER_CLOSED_DEG) / (GRIPPER_OPEN_DEG - GRIPPER_CLOSED_DEG) * 100.0)


class MujocoBackend:
    def __init__(self, sim: So101Sim, grasp: SimpleGrasp | None, *, home_q_deg) -> None:
        self.sim = sim
        self.grasp = grasp
        self.home_q_deg = np.asarray(home_q_deg, dtype=float)
        m = sim.model
        self._act = {
            n: mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_ACTUATOR, n)
            for n in (*JOINT_NAMES, GRIPPER_JOINT)
        }
        self._qpos = {
            n: int(m.jnt_qposadr[mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, n)])
            for n in self._act
        }
        self._dof = {
            n: int(m.jnt_dofadr[mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, n)])
            for n in self._act
        }
        self._owner: CommandToken | None = None
        self._serial = 0
        self.command_log: list[tuple[float, str, object]] = []
        self.reset()

    # --- single command source -------------------------------------------------
    def acquire(self, owner: str) -> CommandToken:
        if self._owner is not None:
            raise NotCommandOwner(f"backend is owned by {self._owner.owner!r}")
        self._serial += 1
        self._owner = CommandToken(owner, self._serial)
        return self._owner

    def release(self, token: CommandToken) -> None:
        self._check(token)
        self._owner = None

    def _check(self, token: CommandToken) -> None:
        if self._owner is None or token != self._owner:
            raise NotCommandOwner(f"token {token} is not the current owner {self._owner}")

    # --- commands ---------------------------------------------------------------
    def command_joints_deg(self, token: CommandToken, q_deg) -> None:
        self._check(token)
        q = np.asarray(q_deg, dtype=float)
        if q.shape != (len(JOINT_NAMES),) or not np.all(np.isfinite(q)):
            raise ValueError(f"bad joint command {q}")
        for n, v in zip(JOINT_NAMES, deg_to_rad(q), strict=True):
            self.sim.data.ctrl[self._act[n]] = v
        self.command_log.append((self.sim.data.time, "joints", q.copy()))

    def command_gripper(self, token: CommandToken, value: float) -> None:
        self._check(token)
        self.sim.data.ctrl[self._act[GRIPPER_JOINT]] = deg_to_rad(gripper_to_deg(value))
        self.command_log.append((self.sim.data.time, "gripper", float(value)))
        if self.grasp is not None:
            if value <= CLOSE_CMD_MAX:
                self.grasp.on_close()
            elif value >= OPEN_CMD_MIN:
                self.grasp.on_open()

    def state(self) -> RobotState:
        d = self.sim.data
        q = rad_to_deg([d.qpos[self._qpos[n]] for n in JOINT_NAMES])
        speed = rad_to_deg(np.abs([d.qvel[self._dof[n]] for n in JOINT_NAMES])).max()
        return RobotState(
            q,
            deg_to_gripper(rad_to_deg(d.qpos[self._qpos[GRIPPER_JOINT]])),
            d.time,
            bool(speed > MOVING_SPEED_DEG_S),
        )

    def stop(self, token: CommandToken) -> None:
        """Freeze: joint targets := current joints, gripper target := current. A held
        object stays held (safer than dropping it). Pending motion is the caller's queue
        and is dropped by the execution layer on the same call."""
        self._check(token)
        d = self.sim.data
        for n in (*JOINT_NAMES, GRIPPER_JOINT):
            d.ctrl[self._act[n]] = d.qpos[self._qpos[n]]
        self.command_log.append((d.time, "stop", None))

    def reset(self) -> None:
        """Back to the home posture with nothing held. Objects are placed by the scene
        owner (execution.trial) before the settle; the owner token is cleared."""
        d = self.sim.data
        mujoco.mj_resetData(self.sim.model, d)
        if self.grasp is not None:
            self.grasp.release_all()
        for n, v in zip(JOINT_NAMES, deg_to_rad(self.home_q_deg), strict=True):
            d.qpos[self._qpos[n]] = v
            d.ctrl[self._act[n]] = v
        open_rad = deg_to_rad(gripper_to_deg(GRIPPER_MAX))
        d.qpos[self._qpos[GRIPPER_JOINT]] = open_rad
        d.ctrl[self._act[GRIPPER_JOINT]] = open_rad
        mujoco.mj_forward(self.sim.model, d)
        self._owner = None
        self.command_log.clear()

    def tick(self, dt_s: float) -> None:
        n = max(1, int(round(dt_s / self.sim.model.opt.timestep)))
        self.sim.step(n)
