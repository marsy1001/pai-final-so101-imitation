"""pick / place primitives, the safety layer and the fixed 0b judgement.

Uses kinematics (targets -> joints), locate (where objects are) and robot (how joints
are commanded). The backend never sees a Cartesian target; every joint target passes
through `safety` first. `criteria` holds the 0b pass/fail numbers fixed on Issue #4.
"""

from .primitives import PickPlace
from .safety import SafetyLimits, SafetyRejected

__all__ = ["PickPlace", "SafetyLimits", "SafetyRejected"]
