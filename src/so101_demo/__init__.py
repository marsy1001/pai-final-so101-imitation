"""SO-101 demo: MuJoCo simulation, kinematics and the pick/place execution layer.

Package map (decided on Issue #3, 2026-09-19). Layers are sub-packages; the two
sim/real replacement boundaries (RobotBackend, position source) get their own.

    assets.py, frames.py, units.py, tolerances.py
        Shared leaf modules: no dependency on any layer. `tolerances.py` holds only
        the model-agreement and IK numeric tolerances fixed before evaluation.
    vendor/        Third-party code with its original header; changes are marked.
    kinematics/    placo FK/IK on the URDF (solver), top-down targets, work area and
                   the rejection checks (top_down), IkRejected (errors).
                   Depends on the leaf modules and vendor only, never on sim.
    sim/           MuJoCo side: model loading, joints, poses, stepping (model).
                   Depends on the leaf modules and mujoco only, never on kinematics.
                   Stage 1 0b adds scene, grasp (simplified-grasp rules) and render.
    evidence/      Generators for docs/evidence/<step>/ (the reviewed artifacts live
                   there). They write only when run as a CLI:
                   `uv run python -m so101_demo.evidence.uc_eval`.

    plan/          Loader/validator for the repository-level contracts/plan.schema.json
                   and contracts/registry.json (never copied into the package).
    robot/         Boundary 1: RobotBackend protocol (5-joint order, gripper 0-100,
                   state, stop, single command source) and the MuJoCo adapter. No IK.
    locate/        Boundary 2: object id -> pose (SimGroundTruth; camera in stage 4).
    execution/     pick/place primitives, safety layer, the fixed 0b judge (criteria)
                   and the trial runner; uses kinematics, locate and robot.

Planned, not yet created:
    recording/, understanding/ (stage 2), app/ after the UI ADR (stage 3).
"""
