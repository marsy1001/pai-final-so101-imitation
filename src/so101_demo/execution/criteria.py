"""0b judgement values and behaviours fixed by the user on 2026-09-19.

Fixed before evaluation; never relaxed to fit a trial. A correction (wrong setup)
is recorded here with the old value and date, and earlier trials keep the
old verdict. Consumed by execution.judge and reported by evidence.stage1_g1.
"""

# pick: object pose and gripper judged independently; the hold list is never used
LIFT_CENTER_Z_M = 0.050  # rest 0.020 + 30 mm
LIFT_HOLD_S = 0.5
GRIPPER_CLOSED_MAX = 40.0  # 0-100
# dropped = the object separated from the gripper between the lift and the release:
# its distance to the TCP grew by more than this over the distance at lift time.
# (Amended 2026-09-19 from "centre z < 0.05 before release": the place descent
# legitimately passes below the lift height while still holding.)
DROP_SEPARATION_M = 0.020

# carry audit (fixed 2026-09-19): the checks above cannot tell
# "held between the jaws" from "somewhere in space with the jaws closed", and cannot tell
# that nothing still constrains the object after the release. Three checks supplement
# them and are part of the verdict: success = judged success AND audit passed.
# A: the object stays inside the grasp zone at every control tick of the carry, not only
#    at the close command. The zone itself is `sim.grasp.GRASP_ZONE_*` (single source;
#    it cannot live here without an import cycle - see the note in that module).
# B: absolute TCP-object distance while carried. Design value: half 20 mm + pocket
#    clearance 3.5 mm; measured worst case 29.9 mm over the five fixed trials 2026-09-19.
# C: no equality constraint is active once the jaws have opened (see RELEASE below).
CARRY_TCP_DIST_MAX_M = 0.045
# true release: the first tick after the lift at which the *measured* gripper value
# (from qpos, not the command) reaches GRIPPER_OPEN_MIN. The trace label `place:released`
# is logged GRIPPER_ACT_S after the open command and is 0.340 s late (measured
# 2026-09-19); the drop window ends and the settle window starts at the true release.

# place
SEAT_CENTER_Z_M = 0.020
SEAT_Z_TOL_M = 0.003
TILT_MAX_DEG = 10.0
SETTLE_LIN_V_MAX_M_S = 0.005
SETTLE_ANG_V_MAX_RAD_S = 0.1
SETTLE_HOLD_S = 0.5
SETTLE_WINDOW_S = 2.0  # after release
GRIPPER_OPEN_MIN = 60.0  # 0-100
TIMEOUT_S = 60.0  # sim time, pick command -> settle verdict

# behaviours
RESET_SETTLE_S = 1.0

FAILURE_CLASSES = (
    "rejected",
    "no_lift",
    "dropped",
    "not_contained",  # carry audit A/B: left the grasp zone, or too far from the TCP
    "still_constrained",  # carry audit C: a constraint was still active after the release
    "unaudited",  # an audit input was missing or invalid at some tick: never a pass
    "wrong_destination",
    "not_seated",
    "not_settled",
    "timeout",
)

# reset complete (fixed 2026-09-19, before the 4a runs): every object back at
# its registered home pose, the arm at the home posture, the jaws open, nothing held and
# no equality constraint active, after RESET_SETTLE_S of settling. Checked after every
# reset, i.e. once when the runner starts and once after every run, rejection or stop; the
# result of the reset that preceded a run is stored with that run (`reset_previous`). A run
# whose reset check fails is not a pass.
RESET_OBJECT_POS_TOL_M = 0.001
RESET_OBJECT_TILT_MAX_DEG = 1.0
RESET_JOINT_TOL_DEG = 1.0
# the gripper-open threshold for the reset check is GRIPPER_OPEN_MIN above
