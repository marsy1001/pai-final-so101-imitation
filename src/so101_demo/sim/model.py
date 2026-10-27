"""MuJoCo side of the SO-101: load the model, drive the five arm joints, read poses.

No rendering: this module only uses mj_forward / mj_step so it runs on a machine
without a GPU or a display.
"""

from __future__ import annotations

import mujoco
import numpy as np

from ..assets import mjcf_path, scene_path
from ..frames import GRIPPER_JOINT, JOINT_NAMES, MJCF_BASE_BODY, MJCF_TIP_SITE, make_transform
from ..units import deg_to_rad


class So101Sim:
    """The SO-101 in MuJoCo. Angles are radians at this boundary (MJCF convention)."""

    def __init__(self, *, with_scene: bool = False, model: mujoco.MjModel | None = None) -> None:
        """Load the bare arm, the upstream scene, or wrap an already compiled model
        (sim.scene builds the stage-1 scene with MjSpec and passes it here)."""
        if model is None:
            path = scene_path() if with_scene else mjcf_path()
            model = mujoco.MjModel.from_xml_path(str(path))
        else:
            path = "<compiled MjSpec>"
        self.model = model
        self.data = mujoco.MjData(self.model)
        self._joint_ids = {n: self._joint_id(n) for n in (*JOINT_NAMES, GRIPPER_JOINT)}
        self._qpos_adr = {n: int(self.model.jnt_qposadr[i]) for n, i in self._joint_ids.items()}
        self._site_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_SITE, MJCF_TIP_SITE)
        self._base_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, MJCF_BASE_BODY)
        if self._site_id < 0 or self._base_id < 0:
            raise ValueError(f"{MJCF_TIP_SITE} or {MJCF_BASE_BODY} missing from {path}")
        mujoco.mj_forward(self.model, self.data)

    def _joint_id(self, name: str) -> int:
        jid = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, name)
        if jid < 0:
            raise ValueError(f"joint {name!r} not found in the model")
        return jid

    def joint_limits_rad(self) -> dict[str, tuple[float, float]]:
        """Joint ranges as the MJCF declares them (radians)."""
        return {
            name: (float(self.model.jnt_range[jid][0]), float(self.model.jnt_range[jid][1]))
            for name, jid in self._joint_ids.items()
        }

    def arm_limits_rad(self) -> np.ndarray:
        limits = self.joint_limits_rad()
        return np.array([limits[n] for n in JOINT_NAMES])

    def set_arm_joints_rad(self, angles_rad) -> None:
        angles = np.asarray(angles_rad, dtype=float)
        if angles.shape != (len(JOINT_NAMES),):
            raise ValueError(f"expected {len(JOINT_NAMES)} joint angles, got {angles.shape}")
        for name, value in zip(JOINT_NAMES, angles, strict=True):
            self.data.qpos[self._qpos_adr[name]] = value
        mujoco.mj_forward(self.model, self.data)

    def set_arm_joints_deg(self, angles_deg) -> None:
        self.set_arm_joints_rad(deg_to_rad(angles_deg))

    def tip_pose(self) -> np.ndarray:
        """World pose of the MJCF tip site as a 4x4 matrix (metres)."""
        return make_transform(
            np.array(self.data.site_xmat[self._site_id]).reshape(3, 3),
            np.array(self.data.site_xpos[self._site_id]),
        )

    def body_pose(self, name: str) -> np.ndarray:
        """World pose of a body as a 4x4 matrix."""
        bid = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, name)
        if bid < 0:
            raise ValueError(f"body {name!r} not found in the model")
        return make_transform(
            np.array(self.data.xmat[bid]).reshape(3, 3), np.array(self.data.xpos[bid])
        )

    def base_pose(self) -> np.ndarray:
        """World pose of the MJCF base body as a 4x4 matrix."""
        return self.body_pose(MJCF_BASE_BODY)

    def tip_site_local_pos(self) -> np.ndarray:
        """Position of the tip site in its parent body, as the MJCF declares it."""
        return np.array(self.model.site_pos[self._site_id])

    def step(self, n: int = 1) -> None:
        for _ in range(n):
            mujoco.mj_step(self.model, self.data)

    def state_is_finite(self) -> bool:
        return bool(
            np.all(np.isfinite(self.data.qpos))
            and np.all(np.isfinite(self.data.qvel))
            and np.all(np.isfinite(self.data.site_xpos))
        )
