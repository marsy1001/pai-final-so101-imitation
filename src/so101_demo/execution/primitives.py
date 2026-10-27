"""pick(object_id) / place(destination_id) on top of the two boundaries.

Sequence:
  pick : pre-grasp above -> descend -> close -> lift
  place: transfer at height -> descend -> open -> retreat
Cartesian waypoints are solved one by one with the top-down IK and checked by the
safety layer; motion is a linear joint interpolation at a fixed control rate.

Placing (Issue #21, 2026-09-20): the jaws open along the cube's registered yaw at the
pick and along the tray's axes at the place, and the place TCP is corrected by the
cube's measured position in the tip frame after the lift, so the cube lands centred
with its edges parallel to the walls (docs/place-margin.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..kinematics import IkRejected, So101Kinematics, solve_top_down, top_down_target
from ..locate import PositionSource
from ..plan import Registry
from ..robot import CommandToken, RobotBackend
from ..sim.grasp import (
    GRASP_ZONE_X_M,
    GRASP_ZONE_Y_M,
    GRASP_ZONE_Z_MAX_M,
    GRASP_ZONE_Z_MIN_M,
)
from .safety import SafetyLimits, SafetyRejected


class TrialTimeout(Exception):
    """The sim-time deadline passed while a primitive was still running."""


CONTROL_DT_S = 0.02  # 50 Hz
TRANSFER_Z_M = (
    0.070  # TCP height for pre-grasp, lift and transfer (cube bottom 0.05 clears walls and cubes)
)
RELEASE_OFFSET_M = 0.005  # open this far above the seated TCP height
SEGMENT_S = 1.0  # time per straight segment
GRIPPER_CLOSE_CMD = 30.0  # 40 mm cube: pads meet at ~32-36; <= GRIPPER_CLOSED_MAX (40)
# Jaw pocket at GRIPPER_CLOSE_CMD is 47 mm wide (measured 2026-09-19). The cube is
# centred in it so neither pad touches a face under the simplified grasp; otherwise
# the fixed pad drags the cube by friction on retreat and tips it.
POCKET_CLEARANCE_M = 0.0035


@dataclass(frozen=True)
class GraspParams:
    """How the jaws are used. Simplified: stop short of the cube and let the weld
    hold. Contact: command fully closed (the position actuator then squeezes the
    cube with its force limit) and put the cube face on the fixed pad."""

    close_cmd: float
    pocket_clearance_m: float

    @classmethod
    def for_config(cls, config_id: str) -> GraspParams:
        if config_id == "sim-v1-simple":
            return cls(GRIPPER_CLOSE_CMD, POCKET_CLEARANCE_M)
        if config_id == "sim-v1-contact":
            return cls(0.0, 0.0)
        raise ValueError(config_id)


def grasp_tcp_for(
    centre: np.ndarray, size_m: float, clearance_m: float, yaw_deg: float = 0.0
) -> np.ndarray:
    """TCP target that puts the cube in the jaw pocket. The tip site lies on the fixed
    jaw's inner face and the moving jaw closes from the site's +Z side (sim.grasp),
    so the TCP goes half a cube (+ clearance) toward -Z of the top-down target frame
    at `yaw_deg` (the jaw-opening direction; the cube's registered yaw at the pick)."""
    R = top_down_target(centre, yaw_deg)[:3, :3]
    return centre - R[:, 2] * (size_m / 2 + clearance_m)


def design_held_offset_m(size_m: float, clearance_m: float) -> np.ndarray:
    """Where the cube centre should sit in the tip-site frame after the close, by design:
    on the jaw-opening axis, half a cube plus the pocket clearance from the site."""
    return np.array([0.0, 0.0, size_m / 2 + clearance_m])


# How far the fixed jaw's collision mesh pushes the cube along the jaw-opening axis during
# the descent, measured on the fixed 0b trials under `sim-v1-simple` (Issue #20 / #21,
# 2026-09-20): the centre sits at 29.6 mm instead of the design 23.5 mm. `place` uses the
# design position plus this push when the held offset is not measured
# (PickPlace.measure_held_offset=False: the real robot until a camera can see the held
# cube, docs/place-margin.md). In the sim the offset is measured after every lift.
GRASP_PUSH_ALONG_JAW_M = 0.0061


def fixed_held_offset_m(size_m: float, clearance_m: float) -> np.ndarray:
    """The unmeasured stand-in for the held offset: design pocket position + measured push."""
    return design_held_offset_m(size_m, clearance_m) + [0.0, 0.0, GRASP_PUSH_ALONG_JAW_M]


def held_offset_is_plausible(offset_m: np.ndarray) -> bool:
    """A measured held offset is used only when the cube is inside the grasp zone (the
    same box the carry audit checks); otherwise the pick missed (or the cube slipped)
    and the reading says nothing about how the cube sits in the jaws."""
    o = np.asarray(offset_m, dtype=float)
    return bool(
        o.shape == (3,)
        and np.all(np.isfinite(o))
        and abs(o[0]) <= GRASP_ZONE_X_M
        and abs(o[1]) <= GRASP_ZONE_Y_M
        and GRASP_ZONE_Z_MIN_M <= o[2] <= GRASP_ZONE_Z_MAX_M
    )


GRIPPER_OPEN_CMD = 100.0
GRIPPER_ACT_S = 0.5
CARTESIAN_STEPS = 8  # waypoints per straight segment (keeps the TCP on the line)


@dataclass
class PickPlace:
    backend: RobotBackend
    kin: So101Kinematics
    locate: PositionSource
    registry: Registry
    safety: SafetyLimits
    token: CommandToken
    grasp: GraspParams = field(default_factory=lambda: GraspParams.for_config("sim-v1-simple"))
    deadline_s: float | None = None  # sim time; None = no deadline
    # Issue #21: `place` puts the cube centre, not the TCP, at the tray centre. The cube's
    # position in the tip frame is measured after the lift from the position source and
    # the FK of the measured joints (sim: ground truth); a reading outside the grasp zone
    # (the pick missed) is discarded for the design value. With False the fixed value
    # `fixed_held_offset_m` is used instead (real robot, docs/place-margin.md).
    measure_held_offset: bool = True
    held_offset_m: np.ndarray | None = None  # set by pick after the lift
    held_offset_source: str | None = None  # "measured" | "design" | "fixed"
    _last_xy: np.ndarray | None = None  # where the transfer starts (pick lift point)
    _last_yaw: float | None = None
    _q_cmd: np.ndarray | None = None
    trace: list[tuple[float, str]] = field(default_factory=list)
    _stopped: bool = False

    def _guard(self) -> None:
        """Called before every command: after stop nothing is sent, after the deadline
        the primitive is abandoned. Both unwind the primitive so no later milestone
        is recorded."""
        if self._stopped:
            raise SafetyRejected("stopped", "no commands after stop")
        if self.deadline_s is not None and self.backend.state().time_s >= self.deadline_s:
            raise TrialTimeout(f"deadline {self.deadline_s} s reached")

    # --- low level ---------------------------------------------------------------
    def _current_cmd(self) -> np.ndarray:
        if self._q_cmd is None:
            self._q_cmd = np.array(self.backend.state().joints_deg)
        return self._q_cmd

    def _move_joints(self, q_target: np.ndarray, duration_s: float) -> None:
        q0 = self._current_cmd().copy()
        n = max(1, int(round(duration_s / CONTROL_DT_S)))
        for i in range(1, n + 1):
            self._guard()
            q = q0 + (q_target - q0) * (i / n)
            self.safety.check_joint_target(q, from_q_deg=self._q_cmd, dt_s=CONTROL_DT_S)
            self.backend.command_joints_deg(self.token, q)
            self._q_cmd = q
            self.backend.tick(CONTROL_DT_S)

    def _solve(self, xyz, yaw_deg: float = 0.0) -> np.ndarray:
        try:
            q = solve_top_down(self.kin, xyz, yaw_deg)
        except IkRejected as e:
            raise SafetyRejected(e.reason, e.detail) from e
        return self.safety.check_joint_target(q)

    def _move_line(
        self,
        xyz_from,
        xyz_to,
        duration_s: float = SEGMENT_S,
        yaw_from_deg: float = 0.0,
        yaw_to_deg: float | None = None,
    ) -> None:
        """Straight TCP line; the grasp yaw is interpolated too (pick yaw -> place yaw on
        the transfer). Every waypoint is solved and checked like any other target."""
        a, b = np.asarray(xyz_from, float), np.asarray(xyz_to, float)
        y0 = float(yaw_from_deg)
        dy = 0.0 if yaw_to_deg is None else float((yaw_to_deg - y0 + 180.0) % 360.0 - 180.0)
        for i in range(1, CARTESIAN_STEPS + 1):
            f = i / CARTESIAN_STEPS
            # the last waypoint is the endpoint itself, not a + (b - a): a target on the
            # work-area boundary (the trays' y = +-0.09) must not drift out by rounding
            xyz = b if i == CARTESIAN_STEPS else a + (b - a) * f
            self._move_joints(self._solve(xyz, y0 + dy * f), duration_s / CARTESIAN_STEPS)

    def _move_to(self, xyz, duration_s: float = SEGMENT_S, yaw_deg: float = 0.0) -> None:
        self._move_joints(self._solve(xyz, yaw_deg), duration_s)

    def _tip_pose(self) -> np.ndarray:
        """Tip pose from the measured joints (FK), available on the real robot too."""
        return self.kin.fk_mjcf_site(self.backend.state().joints_deg)

    def _gripper(self, value: float) -> None:
        self._guard()
        self.backend.command_gripper(self.token, value)
        n = int(round(GRIPPER_ACT_S / CONTROL_DT_S))
        for _ in range(n):
            self._guard()
            self.backend.tick(CONTROL_DT_S)

    def _mark(self, label: str) -> None:
        self.trace.append((self.backend.state().time_s, label))

    def stop(self) -> None:
        """Drop pending motion and freeze the robot. A held object stays held."""
        self._stopped = True
        self.backend.stop(self.token)
        self._q_cmd = None
        self._mark("stop")

    # --- primitives ----------------------------------------------------------------
    def pick(self, object_id: str) -> None:
        self._guard()
        if object_id not in self.registry.objects:
            raise SafetyRejected("unregistered_object", object_id)
        pose = self.locate.pose_of(object_id)
        # x, y from the position source; z from the known object height on the table
        # (handover notes section 7: grasp_z = table_z + size / 2). The measured z is
        # not used: on the real robot it is not observable from a single overhead camera.
        centre = np.array(
            [pose.position_m[0], pose.position_m[1], self.registry.object_rest_z(object_id)]
        )
        obj = self.registry.objects[object_id]
        # the jaws open along the cube's registered yaw (a known value, like z), so the
        # pads face the cube's faces squarely; the measured yaw is not used
        yaw = obj.yaw_deg
        grasp = grasp_tcp_for(centre, obj.size_m, self.grasp.pocket_clearance_m, yaw)
        above = np.array([grasp[0], grasp[1], TRANSFER_Z_M])
        self._mark(f"pick:{object_id}:start")
        self._solve(grasp, yaw)  # refuse before moving if the grasp point is out of reach
        self._move_to(above, yaw_deg=yaw)
        self._mark("pick:pre_grasp")
        self._move_line(above, grasp, yaw_from_deg=yaw)
        self._mark("pick:descended")
        self._gripper(self.grasp.close_cmd)
        self._mark("pick:closed")
        self._move_line(grasp, above, yaw_from_deg=yaw)
        self._mark("pick:lifted")
        self._last_xy = above
        self._last_yaw = yaw
        clearance = self.grasp.pocket_clearance_m
        if not self.measure_held_offset:
            self.held_offset_m = fixed_held_offset_m(obj.size_m, clearance)
            self.held_offset_source = "fixed"
        else:
            T = self._tip_pose()
            measured = T[:3, :3].T @ (self.locate.pose_of(object_id).position_m - T[:3, 3])
            if held_offset_is_plausible(measured):
                self.held_offset_m, self.held_offset_source = measured, "measured"
            else:  # the cube is not between the jaws: place as designed, the judge decides
                self.held_offset_m = design_held_offset_m(obj.size_m, clearance)
                self.held_offset_source = "design"
        self._mark(f"pick:held_offset:{self.held_offset_source}")

    def place(self, destination_id: str, object_id: str) -> None:
        self._guard()
        if destination_id not in self.registry.destinations:
            raise SafetyRejected("unregistered_destination", destination_id)
        dest = self.registry.destinations[destination_id]
        cx, cy = dest.center_xy_m
        obj = self.registry.objects[object_id]
        # the jaws open along the tray's axes, so the cube (picked at its own yaw) is set
        # down with its edges parallel to the walls
        yaw = dest.yaw_deg
        seat_centre = np.array([cx, cy, self.registry.object_rest_z(object_id)])
        # TCP = cube centre - (where the cube sits along the jaw-opening axis): measured
        # after the lift when available, else the design pocket position. Only that axis
        # is corrected: the push happens along it (docs/place-margin.md), the component
        # across the jaws is measurement noise (< 1 mm), z stays the known seat height, and
        # the tray centres sit on the work-area boundary y = +-0.09, where any sideways
        # correction would move the target outside and get it refused.
        held = self.held_offset_m
        if held is None:
            held = design_held_offset_m(obj.size_m, self.grasp.pocket_clearance_m)
        R = top_down_target(seat_centre, yaw)[:3, :3]
        seat_tcp = seat_centre - R[:, 2] * float(held[2])
        above = np.array([seat_tcp[0], seat_tcp[1], TRANSFER_Z_M])
        release = seat_tcp + [0.0, 0.0, RELEASE_OFFSET_M]
        self._mark(f"place:{destination_id}:start")
        if self._last_xy is None:
            self._move_to(above, yaw_deg=yaw)
        else:
            yaw_from = yaw if self._last_yaw is None else self._last_yaw
            self._move_line(self._last_xy, above, SEGMENT_S * 1.5, yaw_from, yaw)
        self._mark("place:above")
        self._move_line(above, release, yaw_from_deg=yaw)
        self._mark("place:descended")
        self._gripper(GRIPPER_OPEN_CMD)
        self._mark("place:released")
        self._move_line(release, above, yaw_from_deg=yaw)
        self._mark("place:retreated")
