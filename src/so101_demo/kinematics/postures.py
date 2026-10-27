"""Fixed joint postures for the placo / MuJoCo agreement check (Issue #3, 0a).

Used by tests/test_kinematics_fk_agreement.py (the 0a evidence generator that shared
it was removed 2026-10-27); the set: zero, five seeded random postures inside 80 % of the joint
ranges, and each joint at each of its two limits (the other joints at zero).
"""

from __future__ import annotations

import numpy as np

from ..frames import JOINT_NAMES
from ..units import rad_to_deg

RANDOM_SEED = 20260919
RANDOM_COUNT = 5
RANGE_FRACTION = 0.8


def posture_names() -> list[str]:
    """Names in the order `agreement_postures_deg` returns them (no model needed)."""
    names = ["zero"] + [f"random{i + 1}" for i in range(RANDOM_COUNT)]
    for name in JOINT_NAMES:
        names += [f"{name}_lower", f"{name}_upper"]
    return names


def agreement_postures_deg(joint_limits_rad: np.ndarray) -> list[tuple[str, np.ndarray]]:
    limits_deg = rad_to_deg(np.asarray(joint_limits_rad, dtype=float))
    n = len(JOINT_NAMES)
    postures: list[tuple[str, np.ndarray]] = [("zero", np.zeros(n))]
    rng = np.random.default_rng(RANDOM_SEED)
    lo, hi = limits_deg[:, 0], limits_deg[:, 1]
    centre, half = (lo + hi) / 2, (hi - lo) / 2 * RANGE_FRACTION
    for i in range(RANDOM_COUNT):
        postures.append((f"random{i + 1}", centre + rng.uniform(-1.0, 1.0, n) * half))
    for j, name in enumerate(JOINT_NAMES):
        for side, value in (("lower", lo[j]), ("upper", hi[j])):
            q = np.zeros(n)
            q[j] = value
            postures.append((f"{name}_{side}", q))
    return postures
