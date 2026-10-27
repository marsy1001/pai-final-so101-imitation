"""Explicit kinematics failures. Angles coming back from a solver prove nothing."""

from __future__ import annotations


class IkRejected(Exception):
    """An end-effector target was refused. `reason` names the check that failed."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail
