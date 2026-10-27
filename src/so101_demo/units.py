"""The single deg/rad boundary. RobotKinematics speaks degrees, MuJoCo radians."""

from __future__ import annotations

import numpy as np


def deg_to_rad(deg) -> np.ndarray:
    return np.deg2rad(np.asarray(deg, dtype=float))


def rad_to_deg(rad) -> np.ndarray:
    return np.rad2deg(np.asarray(rad, dtype=float))
