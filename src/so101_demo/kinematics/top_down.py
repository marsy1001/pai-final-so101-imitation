"""Top-down grasp targets, the stage-1 work area and the rejection checks.

A solver returning angles is not a reached pose (a lesson from the model notes of
2026-09-19). `solve_top_down` therefore checks, in this order: target finite and inside
the work area, solver output finite, joint ranges, position residual, approach
direction, jaw yaw. Any failure raises `IkRejected`; nothing is clipped or silently
accepted.
"""

from __future__ import annotations

import numpy as np

from ..frames import JOINT_NAMES, make_transform
from ..tolerances import IK_GRASP_DIR_TOL_DEG, IK_GRASP_YAW_TOL_DEG, IK_POS_RESIDUAL_TOL_M
from .errors import IkRejected
from .solver import So101Kinematics

# The tip site's local +X is the approach axis: the MJCF site rotation Ry(90 deg) maps
# site +X onto the gripper body's -Z, the direction from the gripper body origin to the
# tip. Checked numerically by tests/test_kinematics_top_down.py.
APPROACH_AXIS_LOCAL = np.array([1.0, 0.0, 0.0])
# The site's local +Z is the jaw-opening direction: the site sits on the fixed jaw's
# inner face and the moving jaw closes from the +Z side (sim.grasp, measured 2026-09-19).
JAW_OPEN_AXIS_LOCAL = np.array([0.0, 0.0, 1.0])
WORLD_DOWN = np.array([0.0, 0.0, -1.0])

# Work area fixed for stage 1 (metres, base frame): a conservative box in front of the
# base inside which every point of a 5x5x4 grid solved a top-down grasp within the
# tolerances under the fixed solver conditions below (2026-09-19; the grid is
# re-solved by tests/test_kinematics_top_down.py). Outside it a target is refused, not clipped.
# The bounds say nothing about physical reachability beyond them: an exploratory scan
# with these seeds found growing residuals farther out and higher up, and that is all.
# 2026-09-19: x lower bound 0.15 -> 0.13 because the grasp TCP sits half a
# cube (20 mm) toward the base from the cube centre and the nearest fixture cube is at
# x = 0.16; z upper bound 0.08 -> 0.07 (the transfer height) so that every grid point
# of the box still solves after the widening. Grid re-checked on 2026-09-19 (earlier
# project; that record was not carried over).
WORKSPACE_MIN = np.array([0.13, -0.09, 0.02])
WORKSPACE_MAX = np.array([0.23, 0.09, 0.07])
# Rounding tolerance of the boundary test, like the 1e-6 rad of joints_within_limits: a
# cube that settled on the table at y = -0.09 reads -0.09000000002, and the tray centres
# are on the boundary y = +-0.09 (2026-09-20). One micrometre absorbs that,
# it does not widen the area (the 0a grid and every fixture are unchanged).
WORKSPACE_TOL_M = 1e-6

# Fixed seeds tried in order (degrees). shoulder_pan is replaced by
# SHOULDER_PAN_PER_YAW * target azimuth: on this model the shoulder body is mounted
# rotated 180 deg about z, so a positive shoulder_pan swings the tip toward -y
# (measured 2026-09-19: pan +30 deg -> tip yaw -27 deg; tests/test_kinematics_top_down.py).
# wrist_roll is replaced by WRIST_ROLL_PER_YAW * (grasp yaw - target azimuth): in a
# top-down pose the roll axis is vertical and a positive roll turns the jaw-opening
# direction toward +y by the same angle (measured 2026-09-20: roll +30 deg
# -> jaw yaw +30 deg), while the pan has already turned the hand by -azimuth.
# The soft IK is a local method: one seed can settle in a wrong basin while another
# reaches the target exactly. The first candidate passing every check is returned;
# if none does the target is rejected. The set is fixed and recorded in the evidence.
SHOULDER_PAN_PER_YAW = -1.0
WRIST_ROLL_PER_YAW = 1.0
IK_SEEDS_DEG = (
    np.array([0.0, -30.0, 60.0, 60.0, 0.0]),
    np.array([0.0, 0.0, 0.0, 90.0, 0.0]),
    np.array([0.0, 30.0, 30.0, 30.0, 0.0]),
    # added 2026-09-19: the three above all settle in a wrong basin at the
    # work-area edge (0.205, -0.09, 0.02); this mid-height seed solves it exactly
    np.array([0.0, -10.0, 30.0, 70.0, 0.0]),
)


def workspace_contains(position) -> bool:
    p = np.asarray(position, dtype=float)
    return bool(
        np.all(p >= WORKSPACE_MIN - WORKSPACE_TOL_M)
        and np.all(p <= WORKSPACE_MAX + WORKSPACE_TOL_M)
    )


def ik_seeds_for(position, yaw_deg: float = 0.0) -> list[np.ndarray]:
    """The fixed seeds with shoulder_pan and wrist_roll set for this target (see the
    note above IK_SEEDS_DEG). Used by solve_top_down and by the 0a evidence scan."""
    p = np.asarray(position, dtype=float)
    azimuth_deg = float(np.degrees(np.arctan2(p[1], p[0])))
    out = []
    for seed in IK_SEEDS_DEG:
        seed = seed.copy()
        seed[0] = SHOULDER_PAN_PER_YAW * azimuth_deg
        seed[4] = WRIST_ROLL_PER_YAW * (float(yaw_deg) - azimuth_deg)
        out.append(seed)
    return out


def top_down_target(position, yaw_deg: float = 0.0) -> np.ndarray:
    """Pose (MJCF site convention) whose approach axis points straight down at `position`.

    The freedom about the approach axis is fixed by `yaw_deg`: the site's local +Z (the
    jaw-opening direction, from the fixed jaw toward the moving jaw) is set to the
    horizontal direction (cos yaw, sin yaw, 0) in the base frame. Until 2026-09-20
    local +Z pointed radially outward from the base, so a cube picked at
    yaw 0 was set down rotated by the destination's azimuth (about 22 deg at the trays)
    and lost most of the placing margin; now pick uses the object's registered yaw and
    place the destination's, so the cube's edges end up parallel to the tray walls.
    wrist_roll, whose axis is vertical in a top-down pose, provides the rotation.
    """
    p = np.asarray(position, dtype=float)
    if p.shape != (3,):
        raise ValueError(f"position must be 3 values, got {p.shape}")
    if not np.all(np.isfinite(p)) or not np.isfinite(yaw_deg):
        raise IkRejected("not_finite", f"target position {p} yaw {yaw_deg}")
    yaw = np.radians(float(yaw_deg))
    z_axis = np.array([np.cos(yaw), np.sin(yaw), 0.0])
    x_axis = WORLD_DOWN
    y_axis = np.cross(z_axis, x_axis)
    return make_transform(np.column_stack([x_axis, y_axis, z_axis]), p)


def grasp_direction_error_deg(T_mjcf_site: np.ndarray) -> float:
    """Angle between the pose's approach axis and straight down (degrees)."""
    approach = T_mjcf_site[:3, :3] @ APPROACH_AXIS_LOCAL
    cos = np.clip(float(approach @ WORLD_DOWN), -1.0, 1.0)
    return float(np.degrees(np.arccos(cos)))


def grasp_yaw_deg(T_mjcf_site: np.ndarray) -> float:
    """Horizontal direction of the pose's jaw-opening axis (site local +Z), degrees."""
    z = T_mjcf_site[:3, :3] @ JAW_OPEN_AXIS_LOCAL
    return float(np.degrees(np.arctan2(z[1], z[0])))


def grasp_yaw_error_deg(T_mjcf_site: np.ndarray, yaw_deg: float) -> float:
    """Angle between the pose's jaw-opening direction and the wanted yaw (degrees, >= 0)."""
    return abs(float((grasp_yaw_deg(T_mjcf_site) - yaw_deg + 180.0) % 360.0 - 180.0))


def evaluate_candidate(
    kin: So101Kinematics, angles_deg: np.ndarray, position: np.ndarray, yaw_deg: float = 0.0
) -> tuple[float, float, float]:
    """Check a solver output; return (position residual m, direction error deg, yaw error
    deg) or raise."""
    if not np.all(np.isfinite(angles_deg)):
        raise IkRejected("not_finite", f"solver returned {angles_deg}")
    inside = kin.joints_within_limits(angles_deg)
    if not np.all(inside):
        names = [n for n, ok in zip(JOINT_NAMES, inside, strict=True) if not ok]
        raise IkRejected("joint_limits", f"{names} outside the model range")
    reached = kin.fk_mjcf_site(angles_deg)
    residual = float(np.linalg.norm(reached[:3, 3] - position))
    if residual > IK_POS_RESIDUAL_TOL_M:
        raise IkRejected("position_residual", f"{residual * 1000:.2f} mm at {position}")
    direction_error = grasp_direction_error_deg(reached)
    if direction_error > IK_GRASP_DIR_TOL_DEG:
        raise IkRejected("grasp_direction", f"{direction_error:.2f} deg at {position}")
    yaw_error = grasp_yaw_error_deg(reached, yaw_deg)
    if yaw_error > IK_GRASP_YAW_TOL_DEG:
        raise IkRejected("grasp_yaw", f"{yaw_error:.2f} deg from yaw {yaw_deg} at {position}")
    return residual, direction_error, yaw_error


def solve_top_down(kin: So101Kinematics, position, yaw_deg: float = 0.0) -> np.ndarray:
    """Angles (degrees) for a top-down grasp at `position` with the jaw-opening direction
    at `yaw_deg` (base frame; 0 = +x), or raise `IkRejected`."""
    p = np.asarray(position, dtype=float)
    target = top_down_target(p, yaw_deg)  # raises on non-finite targets
    if not workspace_contains(p):
        raise IkRejected("outside_workspace", f"{p} not in [{WORKSPACE_MIN}, {WORKSPACE_MAX}]")

    last: IkRejected | None = None
    for seed in ik_seeds_for(p, yaw_deg):
        angles = kin.ik_mjcf_site(target, seed)
        try:
            evaluate_candidate(kin, angles, p, yaw_deg)
        except IkRejected as e:
            last = e
            continue
        return angles
    assert last is not None
    raise IkRejected(last.reason, f"{last.detail} (all {len(IK_SEEDS_DEG)} seeds)")
