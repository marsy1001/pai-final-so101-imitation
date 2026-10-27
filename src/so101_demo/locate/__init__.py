"""Replacement boundary 2: where registered objects are (sim ground truth / detection)."""

from .sim_gt import SimGroundTruth
from .source import ObjectPose, PositionSource

__all__ = ["ObjectPose", "PositionSource", "SimGroundTruth"]
