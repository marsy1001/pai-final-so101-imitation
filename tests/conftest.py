"""Shared fixtures. Rendering tests (`-m gpu`) need the GPU selected before MuJoCo is
imported, so that import comes first."""

from so101_demo.evidence.gl_env import prefer_gpu_on_wsl2

prefer_gpu_on_wsl2()

import pytest  # noqa: E402

from so101_demo.kinematics import So101Kinematics  # noqa: E402
from so101_demo.sim import So101Sim  # noqa: E402


@pytest.fixture(scope="session")
def sim() -> So101Sim:
    return So101Sim()


@pytest.fixture(scope="session")
def kin(sim: So101Sim) -> So101Kinematics:
    return So101Kinematics(sim.arm_limits_rad())
