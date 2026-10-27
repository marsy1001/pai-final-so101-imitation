"""Position source boundary: sim ground truth, and a swap-in fake with the same protocol."""

import numpy as np

from so101_demo.locate import ObjectPose, PositionSource, SimGroundTruth
from so101_demo.sim.scene import SceneConfig, load_scene, set_object_pose


class FakeDetector:
    """Stands in for the stage-4 camera detector: same protocol, fixed answer."""

    def pose_of(self, object_id: str) -> ObjectPose:
        return ObjectPose(
            object_id, np.array([0.2, 0.0, 0.02]), np.eye(3), np.zeros(3), np.zeros(3), 0.0
        )


def test_sim_ground_truth_reads_the_body_pose():
    sim = load_scene(SceneConfig("sim-v1-simple", ("red_cube",)))
    set_object_pose(sim, "red_cube", (0.21, -0.03, 0.02))
    gt = SimGroundTruth(sim, ("red_cube",))
    p = gt.pose_of("red_cube")
    np.testing.assert_allclose(p.position_m, [0.21, -0.03, 0.02], atol=1e-9)
    assert p.rotation.shape == (3, 3) and np.allclose(p.linear_velocity, 0)


def test_sources_are_interchangeable():
    sources: list[PositionSource] = [FakeDetector()]
    sim = load_scene(SceneConfig("sim-v1-simple", ("red_cube",)))
    sources.append(SimGroundTruth(sim, ("red_cube",)))
    for s in sources:
        assert s.pose_of("red_cube").position_m.shape == (3,)
