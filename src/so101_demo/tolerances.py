"""Tolerances fixed before evaluation (Issue #3). Tests and evidence read these, never literals.

FK agreement values were fixed by the user on 2026-09-19 (demo-plan 0a proposal 1 mm / 1 deg).
IK values are the lead's proposal recorded in docs/evidence/0a; they are not gate criteria.
The yaw tolerance was added on Issue #21 (2026-09-20) when the grasp yaw became a target:
2 deg turns a 40 mm cube's footprint by 1.4 mm, well inside the 5 mm placing margin.
"""

FK_POS_TOL_M = 0.001
FK_ROT_TOL_DEG = 1.0

IK_POS_RESIDUAL_TOL_M = 0.005
IK_GRASP_DIR_TOL_DEG = 5.0
IK_GRASP_YAW_TOL_DEG = 2.0
