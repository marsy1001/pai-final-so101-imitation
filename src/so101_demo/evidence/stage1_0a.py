"""Generate docs/evidence/0a/{fk-ik-agreement.md,rejection-cases.md} (Issue #3).

    uv run python -m so101_demo.evidence.stage1_0a [--out DIR]

Writes only when run as a CLI. Measures through the package's public API so the
numbers are the ones the tests check. Needs the repository checkout (assets/so101).
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.metadata
import itertools
import sys
from pathlib import Path

import mujoco
import numpy as np

from .. import tolerances
from ..assets import SO101_REVISION
from ..frames import (
    JOINT_NAMES,
    T_URDF_TIP_TO_MJCF_SITE,
    TIP_LOCAL_POS,
    position_error_m,
    rotation_angle_deg,
)
from ..kinematics import (
    IkRejected,
    So101Kinematics,
    grasp_direction_error_deg,
    solve_top_down,
    top_down_target,
)
from ..kinematics.postures import RANDOM_COUNT, RANDOM_SEED, RANGE_FRACTION, agreement_postures_deg
from ..kinematics.solver import IK_MAX_ITERS, IK_ORIENTATION_WEIGHT, IK_POSITION_WEIGHT
from ..kinematics.top_down import (
    IK_SEEDS_DEG,
    SHOULDER_PAN_PER_YAW,
    WORKSPACE_MAX,
    WORKSPACE_MIN,
    WRIST_ROLL_PER_YAW,
    evaluate_candidate,
    ik_seeds_for,
)
from ..sim import So101Sim
from ..units import rad_to_deg

VENDORED_LEROBOT_REVISION = "5aa74557f84c54d4b458f8b9643c5aa2982acfed"
GRID_YAWS_DEG = (-30.0, -15.0, 15.0, 30.0)  # extra yaws checked on the grid (Issue #21)


def _fmt_vec(v, nd=3) -> str:
    return "(" + ", ".join(f"{x:.{nd}f}" for x in v) + ")"


def _fmt_mat(T: np.ndarray) -> str:
    rows = ["  [" + ", ".join(f"{x:+.6f}" for x in row) + "]" for row in T]
    return "\n".join(rows)


def fk_agreement_md(sim: So101Sim, kin: So101Kinematics, today: str) -> str:
    limits = sim.arm_limits_rad()
    postures = agreement_postures_deg(limits)
    rows = []
    worst_pos = worst_rot = 0.0
    all_ok = True
    for name, q in postures:
        sim.set_arm_joints_deg(q)
        T_mj = np.linalg.inv(sim.base_pose()) @ sim.tip_pose()
        T_pl = np.linalg.inv(kin.base_pose_urdf()) @ kin.fk_mjcf_site(q)
        pos_mm = position_error_m(T_pl, T_mj) * 1000
        rot_deg = rotation_angle_deg(T_pl[:3, :3], T_mj[:3, :3])
        worst_pos, worst_rot = max(worst_pos, pos_mm), max(worst_rot, rot_deg)
        ok = pos_mm <= tolerances.FK_POS_TOL_M * 1000 and rot_deg <= tolerances.FK_ROT_TOL_DEG
        all_ok = all_ok and ok
        rows.append(
            f"| {name} | {_fmt_vec(q, 1)} | {_fmt_vec(T_mj[:3, 3] * 1000, 2)} "
            f"| {pos_mm:.5f} | {rot_deg:.5f} | {'合格' if ok else '不合格'} |"
        )

    # positive IK: centre, 8 corners, 5x5x4 grid
    centre = (WORKSPACE_MIN + WORKSPACE_MAX) / 2
    corners = [
        np.array(c) for c in itertools.product(*zip(WORKSPACE_MIN, WORKSPACE_MAX, strict=True))
    ]
    ik_rows = []
    for name, p in [("centre", centre)] + [(f"corner{i}", c) for i, c in enumerate(corners)]:
        q = solve_top_down(kin, p)
        res, direction, yaw_err = evaluate_candidate(kin, q, p)
        ik_rows.append(
            f"| {name} | {_fmt_vec(p)} | {_fmt_vec(q, 1)} | {res * 1000:.3f} | {direction:.3f} | {yaw_err:.3f} | 合格 |"
        )
    grid = list(
        itertools.product(
            np.linspace(WORKSPACE_MIN[0], WORKSPACE_MAX[0], 5),
            np.linspace(WORKSPACE_MIN[1], WORKSPACE_MAX[1], 5),
            np.linspace(WORKSPACE_MIN[2], WORKSPACE_MAX[2], 4),
        )
    )
    g_worst = (0.0, 0.0, 0.0)
    g_rejected = 0
    for p in grid:
        p = np.array(p)
        try:
            q = solve_top_down(kin, p)
            res, direction, yaw_err = evaluate_candidate(kin, q, p)
            g_worst = (
                max(g_worst[0], res * 1000),
                max(g_worst[1], direction),
                max(g_worst[2], yaw_err),
            )
        except IkRejected:
            g_rejected += 1
    # the same grid at the yaw range the transfer sweeps through (pick yaw 0 at azimuth
    # up to +-35 deg means wrist_roll up to about -+38 deg; Issue #21)
    y_worst, y_rejected = (0.0, 0.0, 0.0), 0
    for p in grid:
        for yaw in GRID_YAWS_DEG:
            try:
                q = solve_top_down(kin, np.array(p), yaw)
                res, direction, yaw_err = evaluate_candidate(kin, q, np.array(p), yaw)
                y_worst = (
                    max(y_worst[0], res * 1000),
                    max(y_worst[1], direction),
                    max(y_worst[2], yaw_err),
                )
            except IkRejected:
                y_rejected += 1

    # exploratory scan straight ahead (y = 0): for each point the candidate with the
    # smallest position residual among the fixed seeds, at the production iteration
    # count and at 100 Newton steps. Diagnostic only; it records residual, direction
    # error and whether that candidate sits on a joint limit. It proves nothing about
    # physical reachability outside the work area.
    reach_rows = []
    max_gain_mm = 0.0
    limits_rad = kin.joint_limits_rad
    for z in (0.02, 0.04, 0.06, 0.08, 0.10, 0.12):
        cells = []
        for x in (0.14, 0.18, 0.22, 0.26, 0.30):
            p = np.array([x, 0.0, z])
            best: dict[int, tuple[float, float, bool] | None] = {IK_MAX_ITERS: None, 100: None}
            for iters in best:
                for seed in ik_seeds_for(p):  # the production seeding (pan / roll set)
                    q = kin.ik_mjcf_site(top_down_target(p), seed, max_iters=iters)
                    reached = kin.fk_mjcf_site(q)
                    res = float(np.linalg.norm(reached[:3, 3] - p)) * 1000
                    if best[iters] is None or res < best[iters][0]:
                        q_rad = np.deg2rad(q)
                        at_limit = bool(
                            np.any(np.abs(q_rad - limits_rad[:, 0]) < 1e-3)
                            or np.any(np.abs(q_rad - limits_rad[:, 1]) < 1e-3)
                        )
                        best[iters] = (res, grasp_direction_error_deg(reached), at_limit)
            r8, d8, l8 = best[IK_MAX_ITERS]
            r100, _, _ = best[100]
            max_gain_mm = max(max_gain_mm, r8 - r100)
            cells.append(f"{r8:.1f} / {d8:.1f}{' L' if l8 else ''} ({r100:.1f})")
        reach_rows.append(f"| z={z:.2f} | " + " | ".join(cells) + " |")

    lim_rows = [
        f"| {n} | {lo:+.4f} | {hi:+.4f} | {rad_to_deg(lo):+.1f} | {rad_to_deg(hi):+.1f} |"
        for n, (lo, hi) in zip(JOINT_NAMES, limits, strict=True)
    ]
    return f"""# 0a: placo FK と MuJoCo の手先 pose の照合記録

- 記録日: {today}。生成: `uv run python -m so101_demo.evidence.stage1_0a`（再現可能）
- 結論: **{len(postures)} 姿勢すべてで位置誤差 {worst_pos:.5f} mm 以下・姿勢誤差 {worst_rot:.5f}° 以下**（許容 {tolerances.FK_POS_TOL_M * 1000:.0f} mm / {tolerances.FK_ROT_TOL_DEG:.0f}°）。変換 1 つで URDF（placo）と MJCF（MuJoCo）の手先 pose が一致する。{"合格" if all_ok else "不合格"}
- 作業領域内の真上把持 IK（顎の向き yaw 0 = base +x）: 中央 + 8 隅 + 5×5×4 格子 {len(grid)} 点で拒否 {g_rejected} 件、最悪 位置残差 {g_worst[0]:.3f} mm / 方向誤差 {g_worst[1]:.3f}° / yaw 誤差 {g_worst[2]:.3f}°（許容 {tolerances.IK_POS_RESIDUAL_TOL_M * 1000:.0f} mm / {tolerances.IK_GRASP_DIR_TOL_DEG:.0f}° / {tolerances.IK_GRASP_YAW_TOL_DEG:.0f}°）。同じ格子 × yaw {", ".join(f"{y:+.0f}" for y in GRID_YAWS_DEG)}° の {len(grid) * len(GRID_YAWS_DEG)} 点で拒否 {y_rejected} 件、最悪 {y_worst[0]:.3f} mm / {y_worst[1]:.3f}° / {y_worst[2]:.3f}°
- 向きの規約（Issue #21、2026-09-20 に変更）: 顎の開く方向（site 局所 +Z）を **base 座標の yaw で指定**する。それまでは base から見た径方向に固定していた（置き先の方位の分だけ立方体が回って着地し、置きの余裕を失っていた）。変更後の数値が上の値。旧規約の記録は git 履歴（`7604201` 以前）
- これはモデル間整合の検査であり、実機較正の合格ではない（handover notes 3 節）

## 条件
| 項目 | 値 |
|---|---|
| SO-101 モデル | TheRobotStudio/SO-ARM100 `Simulation/SO101/` revision `{SO101_REVISION}`（`eecbe3e`）。`so101_new_calib.urdf`（placo）と `so101_new_calib.xml`（MuJoCo）。取得: `scripts/fetch-so101.sh`、`assets/so101/MANIFEST.sha256` で照合 |
| MuJoCo | {mujoco.__version__}（ADR 0005） |
| placo | {importlib.metadata.version("placo")}（`uv.lock`） |
| IK ラッパー | LeRobot `RobotKinematics` revision `{VENDORED_LEROBOT_REVISION}` を `src/so101_demo/vendor/lerobot_kinematics.py` に vendor |
| 5 軸の関節順序 | `{", ".join(JOINT_NAMES)}`（名前で対応付け。gripper は別扱い） |
| 単位境界 | placo = deg、MuJoCo = rad。`so101_demo.units.deg_to_rad` / `rad_to_deg` の各 1 関数 |
| base | URDF `base_link`・MJCF body `base` とも world 原点・単位姿勢（両方を assert）。比較は `T_base^-1 T_tip` 同士 |
| 手先 | URDF `gripper_frame_link`、MJCF site `gripperframe`。局所位置は両方 {_fmt_vec(TIP_LOCAL_POS, 7)} m |
| 採用した変換 | `T_mjcf_site = T_urdf_tip · T_URDF_TIP_TO_MJCF_SITE`、回転 Ry(-90°)・並進 0（`so101_demo.frames`）。根拠: URDF `rpy="0 π 0"` = Ry(180°)、MJCF `quat=(0.707107, 0, 0.707107, 0)` = Ry(90°) |
| 許容値 | FK 位置 {tolerances.FK_POS_TOL_M * 1000:.0f} mm・姿勢 {tolerances.FK_ROT_TOL_DEG:.0f}°: ユーザーが 2026-09-19 に Issue #3 で固定（demo-plan 0a 提案値）。IK 位置残差 {tolerances.IK_POS_RESIDUAL_TOL_M * 1000:.0f} mm・把持方向 {tolerances.IK_GRASP_DIR_TOL_DEG:.0f}°: lead の提案（Issue #3 コメント、ゲート判定値ではない）。顎 yaw {tolerances.IK_GRASP_YAW_TOL_DEG:.0f}°: lead の提案（Issue #21、2026-09-20。40 mm 立方体の足元が 1.4 mm 広がる角度）。定義は `so101_demo.tolerances` の 1 箇所 |

`T_URDF_TIP_TO_MJCF_SITE` =
```
{_fmt_mat(T_URDF_TIP_TO_MJCF_SITE)}
```

関節範囲（MJCF `jnt_range`。URDF の `limit` と同値）:

| 関節 | 下限 rad | 上限 rad | 下限 deg | 上限 deg |
|---|---|---|---|---|
{chr(10).join(lim_rows)}

## FK 照合（{len(postures)} 姿勢）
姿勢の選び方（`so101_demo.kinematics.postures`）: ゼロ、関節範囲の {RANGE_FRACTION:.0%} 内の乱数 {RANDOM_COUNT} 姿勢（seed {RANDOM_SEED}）、各関節を上下限に置いた 10 姿勢。角度は deg、順序は上記 5 軸。

| 姿勢 | 関節角 deg | MuJoCo 手先位置 mm | 位置誤差 mm | 姿勢誤差 ° | 判定 |
|---|---|---|---|---|---|
{chr(10).join(rows)}

## 真上把持 IK（作業領域内）
- 作業領域（base 座標、m）: x ∈ [{WORKSPACE_MIN[0]:.2f}, {WORKSPACE_MAX[0]:.2f}]、y ∈ [{WORKSPACE_MIN[1]:.2f}, {WORKSPACE_MAX[1]:.2f}]、z ∈ [{WORKSPACE_MIN[2]:.2f}, {WORKSPACE_MAX[2]:.2f}]（`so101_demo.kinematics.top_down`）。外は拒否、クリップしない
- 目標姿勢: site の局所 +X（接近軸。site の Ry(90°) が gripper body の -Z に対応）を world -Z に向け、局所 +Z（顎の開く方向。固定顎から可動顎へ）を **base 座標の yaw**（既定 0 = +x）に向ける。pick は物体の登録 yaw、place は置き先の yaw（トレイは軸平行、0）を使う（Issue #21）。真上姿勢では wrist_roll の軸が鉛直なので yaw は wrist_roll で自由に選べる
- solver: placo soft frame task、position_weight {IK_POSITION_WEIGHT}、orientation_weight {IK_ORIENTATION_WEIGHT}、Newton {IK_MAX_ITERS} 回（LeRobot 既定）。seed は固定 {len(IK_SEEDS_DEG)} 組（shoulder_pan は目標の方位角 × {SHOULDER_PAN_PER_YAW:+.0f} に置換。この機体は shoulder body が z 軸まわりに 180° 回して取り付けられており、pan +30° で手先 yaw は −27°。wrist_roll は (顎 yaw − 方位角) × {WRIST_ROLL_PER_YAW:+.0f} に置換。roll +30° で顎の向きは +30° 回る、2026-09-20 実測）を順に試し、全検査を通った最初の解を返す: {", ".join(_fmt_vec(s, 0) for s in IK_SEEDS_DEG)}
- 検査順: 目標 finite → 作業領域内 → 解 finite → 関節限界 → 位置残差 → 接近方向 → 顎 yaw。どれかで `IkRejected`
- solver が角度を返すことを到達成功とは扱わない。solver の不成功も物理的な到達不能の証明ではない（seed と条件に依存する）

| 点 | 目標 m（yaw 0） | 解 deg | 位置残差 mm | 方向誤差 ° | yaw 誤差 ° | 判定 |
|---|---|---|---|---|---|---|
{chr(10).join(ik_rows)}

格子 {len(grid)} 点（5×5×4、yaw 0）: 拒否 {g_rejected} 件、最悪 {g_worst[0]:.3f} mm / {g_worst[1]:.3f}° / {g_worst[2]:.3f}°。同じ格子 × yaw {", ".join(f"{y:+.0f}" for y in GRID_YAWS_DEG)}°（{len(grid) * len(GRID_YAWS_DEG)} 点）: 拒否 {y_rejected} 件、最悪 {y_worst[0]:.3f} mm / {y_worst[1]:.3f}° / {y_worst[2]:.3f}°。

### 作業領域の決め方と、領域外の探索的診断（y = 0）
作業領域は**今回サポートする保守的な固定領域**で、根拠は上の格子 {len(grid)} 点が現条件（固定 {len(IK_SEEDS_DEG)} seed・上記の目標姿勢・Newton {IK_MAX_ITERS} 回）で全て通ったこと。下表は領域の外側も含めた探索的な診断で、各セルは「固定 seed のうち位置残差が最小の候補」の 位置残差 mm / 方向誤差 °、`L` = その候補のいずれかの関節が限界上（1e-3 rad 以内）、括弧内 = Newton 100 回での位置残差。Newton 回数を {IK_MAX_ITERS} → 100 に増やしたときの改善は最大 {max_gain_mm:.3f} mm（{today}、本生成で再計算）。

この表は **固定 seed・固定姿勢・位置残差の探索であり、領域外の物理的な到達不能を示さない**（handover notes 2 節: solver の失敗を到達不能の証明にしない）。残差が大きいセルは「この条件では解けなかった」以上の意味を持たない。領域を広げるときは、seed・姿勢制約を変えたうえで格子検査をやり直す。

| | x=0.14 | x=0.18 | x=0.22 | x=0.26 | x=0.30 |
|---|---|---|---|---|---|
{chr(10).join(reach_rows)}

## 参照
- `docs/demo-plan.md`「sim の設計」0a 行、`docs/handover/mujoco-so101-notes.md` 2〜3 節
- テスト: `tests/test_kinematics_fk_agreement.py`、`tests/test_kinematics_top_down.py`（`uv run pytest -q`）
"""


def rejection_cases_md(kin: So101Kinematics, today: str) -> str:
    centre = (WORKSPACE_MIN + WORKSPACE_MAX) / 2
    cases: list[tuple] = [
        (
            "作業領域外 (+x)",
            _fmt_vec(WORKSPACE_MAX + [0.05, 0, 0]),
            lambda: solve_top_down(kin, WORKSPACE_MAX + [0.05, 0, 0]),
        ),
        (
            "作業領域外 (-y)",
            _fmt_vec(WORKSPACE_MIN - [0, 0.05, 0]),
            lambda: solve_top_down(kin, WORKSPACE_MIN - [0, 0.05, 0]),
        ),
        (
            "作業領域外 (+z)",
            _fmt_vec([centre[0], centre[1], WORKSPACE_MAX[2] + 0.10]),
            lambda: solve_top_down(kin, [centre[0], centre[1], WORKSPACE_MAX[2] + 0.10]),
        ),
        (
            "作業領域外 (遠方 x=0.45)",
            "(0.450, 0.000, 0.020)",
            lambda: solve_top_down(kin, [0.45, 0.0, 0.02]),
        ),
        ("領域内だが全 seed が方向検査に落ちる (許容 -1° に設定)", "(centre)", "all_seeds"),
        (
            "不正値: 目標に NaN",
            "(nan, 0.000, 0.050)",
            lambda: solve_top_down(kin, [np.nan, 0.0, 0.05]),
        ),
        (
            "不正値: 関節角に NaN",
            "q = (0, nan, 0, 0, 0)",
            lambda: kin.fk_mjcf_site([0.0, np.nan, 0.0, 0.0, 0.0]),
        ),
        (
            "不正値: 関節限界外 (shoulder_lift 150°)",
            "q = (0, 150, 0, 0, 0)",
            lambda: evaluate_candidate(kin, np.array([0.0, 150.0, 0.0, 0.0, 0.0]), centre),
        ),
        (
            "位置残差超過 (ゼロ姿勢を中央の解として検査)",
            "q = (0, 0, 0, 0, 0)",
            lambda: evaluate_candidate(kin, np.zeros(5), centre),
        ),
        ("把持方向超過 (位置検査を無効化したゼロ姿勢)", "q = (0, 0, 0, 0, 0)", None),
        (
            "顎 yaw 超過 (yaw 0 の解を yaw 30° の目標として検査)",
            "q = solve_top_down(centre, yaw 0)",
            lambda: evaluate_candidate(kin, solve_top_down(kin, centre, 0.0), centre, 30.0),
        ),
    ]
    rows = []
    from ..kinematics import top_down as td

    for name, inp, fn in cases:
        if fn == "all_seeds":
            saved = td.IK_GRASP_DIR_TOL_DEG
            td.IK_GRASP_DIR_TOL_DEG = -1.0
            try:
                solve_top_down(kin, centre)
                result = "**暗黙に成功（不合格）**"
            except IkRejected as e:
                result = f"`IkRejected({e.reason})` {e.detail}"
            finally:
                td.IK_GRASP_DIR_TOL_DEG = saved
        elif fn is None:
            saved = td.IK_POS_RESIDUAL_TOL_M
            td.IK_POS_RESIDUAL_TOL_M = 10.0
            try:
                evaluate_candidate(kin, np.zeros(5), centre)
                result = "**暗黙に成功（不合格）**"
            except IkRejected as e:
                result = f"`IkRejected({e.reason})` {e.detail}"
            finally:
                td.IK_POS_RESIDUAL_TOL_M = saved
        else:
            try:
                fn()
                result = "**暗黙に成功（不合格）**"
            except IkRejected as e:
                result = f"`IkRejected({e.reason})` {e.detail}"
        rows.append(f"| {name} | {inp} | {result} |")
    raw = kin.ik_mjcf_site(top_down_target([0.45, 0.0, 0.02]), np.zeros(5))
    return f"""# 0a: 拒否ケースの記録

- 記録日: {today}。生成: `uv run python -m so101_demo.evidence.stage1_0a`
- 結論: 作業領域外・不正値（NaN、関節限界外）・位置残差超過・把持方向超過・顎 yaw 超過の各入力で `IkRejected` が上がり、暗黙に成功したものはない
- 検査順（`so101_demo.kinematics.top_down`）: 目標 finite → 作業領域内 → 解 finite → 関節限界 → 位置残差 → 接近方向 → 顎 yaw（Issue #21 で追加、2026-09-20）
- 許容値: 位置残差 {tolerances.IK_POS_RESIDUAL_TOL_M * 1000:.0f} mm、把持方向 {tolerances.IK_GRASP_DIR_TOL_DEG:.0f}°、顎 yaw {tolerances.IK_GRASP_YAW_TOL_DEG:.0f}°（`so101_demo.tolerances`）

| ケース | 入力 | 結果 |
|---|---|---|
{chr(10).join(rows)}

補足: 生の solver（`So101Kinematics.ik_mjcf_site`）は作業領域外の遠方目標 (0.45, 0, 0.02) にも有限の角度 {_fmt_vec(raw, 1)} deg を返す（この角度での FK は目標に届かない）。`solve_top_down` はこれを検査で拒否する。「戻り値がある = 到達成功」ではない。到達可能かどうか自体はこの記録では判定していない。

- テスト: `tests/test_kinematics_top_down.py`
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=Path("docs/evidence/0a"))
    args = ap.parse_args(argv)
    today = dt.date.today().isoformat()
    sim = So101Sim()
    kin = So101Kinematics(sim.arm_limits_rad())
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "fk-ik-agreement.md").write_text(fk_agreement_md(sim, kin, today), encoding="utf-8")
    (args.out / "rejection-cases.md").write_text(rejection_cases_md(kin, today), encoding="utf-8")
    print(f"wrote {args.out / 'fk-ik-agreement.md'} and {args.out / 'rejection-cases.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
