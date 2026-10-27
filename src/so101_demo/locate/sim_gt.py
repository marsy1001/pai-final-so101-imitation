from __future__ import annotations

import mujoco
import numpy as np

from ..sim.model import So101Sim
from .source import ObjectPose


class SimGroundTruth:
    def __init__(self, sim: So101Sim, object_ids: tuple[str, ...]) -> None:
        self.sim = sim
        m = sim.model
        self._body = {o: mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, o) for o in object_ids}
        self._dof = {
            o: int(m.jnt_dofadr[mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, f"{o}_free")])
            for o in object_ids
        }
        missing = [o for o, b in self._body.items() if b < 0]
        if missing:
            raise ValueError(f"objects not in scene: {missing}")

    def pose_of(self, object_id: str) -> ObjectPose:
        d = self.sim.data
        b, dof = self._body[object_id], self._dof[object_id]
        return ObjectPose(
            object_id,
            np.array(d.xpos[b]),
            np.array(d.xmat[b]).reshape(3, 3),
            np.array(d.qvel[dof : dof + 3]),
            np.array(d.qvel[dof + 3 : dof + 6]),
            float(d.time),
        )
