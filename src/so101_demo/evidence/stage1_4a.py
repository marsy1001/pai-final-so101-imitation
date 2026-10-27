"""Generate docs/evidence/4a/runs.md (+ runs.json): the fixed 4a fixtures through the runner.

    uv run python -m so101_demo.evidence.stage1_4a --approve-fixed-fixture [--label 参考]
    MUJOCO_GL=egl uv run python -m so101_demo.evidence.stage1_4a --approve-fixed-fixture --label 受け入れ --video

Without `--approve-fixed-fixture` nothing runs: the fixtures are development input and
the flag is the only development path that marks them approved (docs/execution-runner.md).
The run: the four fixtures in order (object switched every time, reset between runs),
then the rejection scenarios and one stop, each recorded with the commands the robot
received. Judgement values come from execution.criteria; the fixtures from
contracts/fixtures/4a. A laptop run is labelled 参考; the acceptance run happens on the
GPU machine with `--video`, one MP4 per fixture (`<fixture>-<config>.mp4`).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import platform
import sys
from pathlib import Path

import mujoco

from ..execution import criteria as C
from ..execution.runner import (
    PlanRunner,
    RunnerState,
    RunRecord,
    approve_for_development,
    parse_plan,
)
from ..kinematics.top_down import WORKSPACE_MAX
from ..plan import load_registry
from ..plan.paths import FIXTURES_4A_DIR
from ..sim.scene import CONFIG_IDS
from .record import VideoRecorder
from .stage1_0b import _gpu_name

OUT_OF_RANGE_XYZ = (round(float(WORKSPACE_MAX[0]) + 0.17, 3), 0.02, 0.02)  # beyond the work area
DISPLACED_XYZ = (0.16, -0.03, 0.02)  # red_cube moved out of the pocket before the close

# Rejection inputs (R1, R3) are text, as they would arrive from the understanding layer.
GREEN_BALL = json.dumps(
    {
        "status": "ok",
        "target": "green_ball",
        "destination": "box_a",
        "steps": [
            {"action": "pick", "object": "green_ball"},
            {"action": "place", "destination": "box_a"},
        ],
        "note": "未登録の物体",
    },
    ensure_ascii=False,
)
INVALID_JSON = {
    "R3a 構文エラー": '{"status": "ok", "target": "red_cube",',
    "R3b 必須項目欠落 (note なし)": json.dumps(
        {
            "status": "ok",
            "target": "red_cube",
            "destination": "box_a",
            "steps": [
                {"action": "pick", "object": "red_cube"},
                {"action": "place", "destination": "box_a"},
            ],
        }
    ),
    "R3c ok で steps 空": json.dumps(
        {
            "status": "ok",
            "target": "red_cube",
            "destination": "box_a",
            "steps": [],
            "note": "x",
        }
    ),
    "R3d target と steps の不整合": json.dumps(
        {
            "status": "ok",
            "target": "red_cube",
            "destination": "box_a",
            "steps": [
                {"action": "pick", "object": "blue_cube"},
                {"action": "place", "destination": "box_a"},
            ],
            "note": "x",
        }
    ),
}


def fixtures() -> list[Path]:
    fs = sorted(FIXTURES_4A_DIR.glob("*.json"))
    if len(fs) != 4:
        raise SystemExit(f"expected 4 fixtures in {FIXTURES_4A_DIR}, found {len(fs)}")
    return fs


def approved(path: Path, flag: bool):
    return approve_for_development(
        parse_plan(path.read_text(encoding="utf-8")), path.stem, approve_fixed_fixture=flag
    )


def run_all(
    config_id: str, label: str, out: Path, video: bool, flag: bool
) -> tuple[list[RunRecord], list[tuple[str, str, RunRecord]]]:
    rec_holder: dict[str, VideoRecorder] = {}

    def observer(sim):
        def cb(s):
            if "r" in rec_holder:
                rec_holder["r"](s)

        return cb

    runner = PlanRunner(config_id, observer=observer if video else None)
    fixture_runs: list[RunRecord] = []
    for f in fixtures():
        path = out / f"{f.stem}-{config_id}.mp4"
        if video:
            rec_holder["r"] = VideoRecorder(runner.sim, path)
        try:
            rec = runner.submit(approved(f, flag))
        finally:
            size = rec_holder.pop("r").close() if "r" in rec_holder else None
        if video:
            rec.video_path, rec.video_bytes = path.name, size
        fixture_runs.append(rec)

    scenarios: list[tuple[str, str, RunRecord]] = []
    f1 = fixtures()[0]

    # R0 unapproved: the same fixture, validated but without the approval mark
    scenarios.append(
        (
            "R0 承認マーク無し",
            "F1 の JSON を検証済み Plan のまま submit（承認関数を通さない）",
            runner.submit(parse_plan(f1.read_text(encoding="utf-8")), f1.stem),
        )
    )
    # R1 unrelated object
    scenarios.append(
        ("R1 無関係な物体", "未登録 ID `green_ball` の ok プラン", runner.submit(GREEN_BALL))
    )
    # R2 out of range: the target sits outside the work area
    runner.place_object("red_cube", OUT_OF_RANGE_XYZ)
    scenarios.append(
        (
            "R2 範囲外",
            f"red_cube を作業領域外 {OUT_OF_RANGE_XYZ} に置いて F1 を承認付きで submit",
            runner.submit(approved(f1, flag)),
        )
    )
    # R3 invalid JSON, four variants
    for name, text in INVALID_JSON.items():
        scenarios.append((name, f"`{text[:60]}`", runner.submit(text)))
    # R4 close far from the object: displaced after the pre-grasp, before the descent

    def displace(r: PlanRunner) -> None:
        if r._pp is not None and r._pp.trace and r._pp.trace[-1][1] == "pick:pre_grasp":
            r.place_object("red_cube", DISPLACED_XYZ)

    runner.on_tick = displace
    try:
        rec = runner.submit(approved(f1, flag))
    finally:
        runner.on_tick = None
    scenarios.append(
        (
            "R4 離れた位置での閉指令",
            f"pre_grasp 到達時に red_cube を {DISPLACED_XYZ} へ移し、閉指令が空振りする",
            rec,
        )
    )
    # S1 stop mid-run (after the close, during the lift)

    def stop_after_close(r: PlanRunner) -> None:
        if r._pp is not None and r._pp.trace and r._pp.trace[-1][1] == "pick:closed":
            r.stop()

    runner.on_tick = stop_after_close
    try:
        rec = runner.submit(approved(f1, flag))
    finally:
        runner.on_tick = None
    scenarios.append(
        ("S1 実行中の停止", "閉指令の直後に stop()。保持したまま静止 → リセットで解放", rec)
    )
    # B1 busy: a second submission while one runs
    inner: dict[str, RunRecord] = {}

    def submit_while_running(r: PlanRunner) -> None:
        if "busy" not in inner and r.state is RunnerState.RUNNING:
            inner["busy"] = r.submit(approved(f1, flag))

    runner.on_tick = submit_while_running
    try:
        outer = runner.submit(approved(f1, flag))
    finally:
        runner.on_tick = None
    busy = inner["busy"]
    scenarios.append(
        (
            "B1 実行中の再投入",
            "F1 実行中に F1 をもう一度 submit（外側の実行は通常どおり完了）",
            busy,
        )
    )
    scenarios.append(("B1 外側の実行", "上の再投入を受けた側の実行", outer))
    return fixture_runs, scenarios


def _states(r: RunRecord) -> str:
    return " → ".join(f"{t.state.value}" for t in r.transitions) or "-"


def _reset(rc) -> str:
    if rc is None:
        return "-"
    pos = max(rc.object_pos_err_m.values()) * 1000 if rc.object_pos_err_m else 0.0
    tilt = max(rc.object_tilt_deg.values()) if rc.object_tilt_deg else 0.0
    return (
        f"{'合' if rc.passed else '否'} (物体 {pos:.2f} mm / {tilt:.2f}°, 関節 {rc.joint_err_deg:.2f}°, "
        f"開 {rc.gripper:.0f}, 拘束 {rc.constraints_active})"
    )


def render(fixture_runs, scenarios, config_id, label, today, video) -> str:
    reg = load_registry()
    n_ok = sum(r.outcome == "success" for r in fixture_runs)
    rows = []
    for r in fixture_runs:
        v = r.verdict
        if v is None:  # rejected before any motion (previous reset failed, busy)
            rows.append(
                f"| {r.fixture_id} | {r.plan['target'] if r.plan else '-'} | {r.plan['destination'] if r.plan else '-'} "
                f"| {r.outcome} | - | - | - | - | - | {r.commands_sent} | {_states(r)} | {_reset(r.reset_previous)} "
                f"| {_reset(r.reset_after)} | - | - | {r.wall_time_s:.1f} | {(r.reason or '')[:80]} |"
            )
            continue
        disp = max(r.other_objects_max_displacement_m.values()) * 1000
        rows.append(
            f"| {r.fixture_id} | {r.plan['target']} | {r.plan['destination']} | {r.outcome} "
            f"| {v.max_lift_z:.4f} | {'-' if v.final_center is None else f'({v.final_center[0]:.4f}, {v.final_center[1]:.4f})'} "
            f"| {'-' if v.final_tilt_deg is None else f'{v.final_tilt_deg:.2f}'} "
            f"| {v.details.get('carry_max_tcp_distance_m', 0) * 1000:.1f} / {v.details.get('carry_max_zone_excursion_m', 0) * 1000:.1f} / {v.details.get('release_samples_audited', '-')} "
            f"| {disp:.2f} | {r.commands_sent} | {_states(r)} | {_reset(r.reset_previous)} | {_reset(r.reset_after)} "
            f"| {'-' if r.video_path is None else f'`{r.video_path}` {r.video_bytes / 1e6:.1f} MB'} | {r.sim_time_s:.1f} | {r.wall_time_s:.1f} | {'録画失敗: ' + r.observer_error if r.observer_error else ''} |"
        )
    srows = []
    for name, setup, r in scenarios:
        moved = "-" if r.verdict is None else f"{r.verdict.max_lift_z:.4f}"
        at = ", ".join(f"{k} ({v[0]:.2f}, {v[1]:.2f})" for k, v in r.scene_at_submit.items())
        srows.append(
            f"| {name} | {setup} | {r.outcome} | {(r.reason or '-')[:110]} | {r.commands_sent} | {moved} | {at} | {_states(r)} | {_reset(r.reset_after)} |"
        )
    homes = ", ".join(f"{k} {tuple(v.home_xyz_m)}" for k, v in reg.objects.items())
    gpu = _gpu_name()
    machine = f"{platform.node()}、{platform.machine()}、{gpu or 'NVIDIA GPU なし'}"
    if label == "参考":
        position = "**参考実行**（受け入れ実行ではない）。描画を使わない headless 実行。受け入れ判定（4/4、動画付き）は母艦分で行う（ADR 0006）"
    else:
        position = f"**受け入れ実行**（母艦: {machine}。ADR 0006）。" + (
            "各 fixture を固定カメラから録画し「動画」列に記す（`so101_demo.evidence.record`、`MUJOCO_GL=egl`）"
            if video
            else "動画なし"
        )
    cmd = f"{'MUJOCO_GL=egl ' if video else ''}uv run python -m so101_demo.evidence.stage1_4a --approve-fixed-fixture --label {label}{' --video' if video else ''}"
    return f"""# 4a 実行側: 固定 JSON → 4 組合せ・拒否試験・停止の実行ログ（{label}）

- 記録日: {today}。生成: `{cmd}`（再現可能）
- 結論: 固定 fixture 4 組合せ —— `{config_id}` **{n_ok}/4 成功**。拒否試験・停止・再投入は下表（いずれも関節指令 0 または停止で終わる）
- 位置づけ: {position}
- この記録は「承認済みと明示した開発用固定 JSON から sim が動く」証拠である。録画理解（第 2 段）の証拠にはしない。G1 の固定 10 試行はこの記録では行わない
- 入口は `so101_demo.execution.runner.PlanRunner.submit` の 1 つ。承認マーク（`ApprovedPlan`）は `--approve-fixed-fixture`（開発用）と承認画面（第 2 段 3b、未実装）の 2 経路だけが作る（`docs/execution-runner.md`）
- 以前の記録は上書きしない: 旧配置（トレイ (0.19, ±0.085)、2026-09-20 ADR 0008 で変更）の記録は `tray19/`: 受け入れ実行 `tray19/runs.md`、XPS 分の参考実行（母艦上の headless、2026-09-19、`sim-v1-simple` 参考 4/4）`tray19/runs-reference-xps.md` / `.json`。新配置で置き余裕を直す前（Issue #20、`7604201`、参考 4/4）の記録は `issue20/runs.md` / `.json`。受け入れ実行は同じ fixture・同じ判定値で動画付き
- 動画: 各 fixture の 実行中 区間（submit から 完了 まで、リセットは含まない）を `evidence/record.py` の固定カメラで 640×480・25 fps に録画。1 本 10 MB 以内（Issue #9）。1080p・ラベル・状態表示は次の 見栄え Issue の範囲
- 判定は 0b と同じ（`execution.judge`、`execution.criteria`）。成功 = 判定器 success かつ搬送監査 A/B/C 通過 **かつ実行後のリセット判定 合**（リセット不合格は `reset_failed` として成功に数えない）。承認済みプランも指令前に契約を再検証する（`recheck_plan`）。リセット完了は Issue #9 で固定した値で判定し、各実行に「前回リセットの結果」と「実行後のリセットの結果」を残す（submit 時の物体位置は別に記録。G1 では home 以外から始める試行があるため、submit 時に home にあることは要求しない）

## 共通条件
| 項目 | 値 |
|---|---|
| 機材 | {machine}、Python {platform.python_version()}、MuJoCo {mujoco.__version__} |
| 構成 | `{config_id}`（簡略把持。ADR 0007 Accepted 2026-09-19。接触設定の変更 0/1） |
| シーン | 登録物体 2 個を同時に配置。home: {homes}（Issue #9、2026-09-19 に y = 0 から ±20 mm へ移動。理由は注記） |
| リセット | `mj_resetData` → ホーム姿勢・開・weld 全解除 → 両物体を home へ → {C.RESET_SETTLE_S} s 静定 → 判定: 物体位置差 ≤ {C.RESET_OBJECT_POS_TOL_M * 1000:.0f} mm・傾き ≤ {C.RESET_OBJECT_TILT_MAX_DEG:.0f}°、関節 ≤ {C.RESET_JOINT_TOL_DEG:.0f}°、開 ≥ {C.GRIPPER_OPEN_MIN:.0f}、保持なし、有効拘束 0、1 mm 超の貫入なし |
| 状態遷移 | 待機 → 実行中 → 完了 → リセット → 待機。動作前の拒否（未承認・不正 JSON）は 完了（理由付き）→ リセット → 待機。安全層の拒否・失敗・停止・想定外の例外は 実行中 を経て 完了 → リセット → 待機。実行中の再投入は `busy` として拒否し遷移を記録しない |
| 判定値 | 0b と同一: 持ち上げ 中心 z ≥ {C.LIFT_CENTER_Z_M} m を {C.LIFT_HOLD_S} s、着座 底面 4 隅が内寸内・中心 z {C.SEAT_CENTER_Z_M} ± {C.SEAT_Z_TOL_M} m・傾き ≤ {C.TILT_MAX_DEG}°、静止 v < {C.SETTLE_LIN_V_MAX_M_S * 1000:.0f} mm/s かつ ω < {C.SETTLE_ANG_V_MAX_RAD_S} rad/s を {C.SETTLE_HOLD_S} s、時間上限 {C.TIMEOUT_S:.0f} s、搬送監査 TCP 距離 ≤ {C.CARRY_TCP_DIST_MAX_M * 1000:.0f} mm・把持ゾーン内・解放後拘束 0 |
| 正本 | fixture: `contracts/fixtures/4a/`。物体・置き先・home: `contracts/registry.json`。判定値: `src/so101_demo/execution/criteria.py`。実行器: `src/so101_demo/execution/runner.py` |

## 4 組合せ（`{config_id}`、{label}）: **{n_ok}/4 成功**
順に実行し、毎回リセット。「他物体の移動」= 対象でない物体が実行中に home から動いた最大距離。

| fixture | 対象 | 置き先 | 結果 | 持ち上げ最大 z m | 解放後 中心 xy m | 傾き ° | 監査: TCP 距離最大 mm / ゾーン逸脱 mm / 解放後監査 tick | 他物体の移動 mm | 送った指令数 | 状態遷移 | リセット判定（前回リセット） | リセット判定（後） | 動画 | 実行 sim 時間 s | 壁時計 s | 備考 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
{chr(10).join(rows)}

## 拒否試験・停止・再投入
「送った指令数」= その submit で RobotBackend が受けた関節・グリッパー・stop 指令の数。拒否（`rejected`）は 0 でなければならない。R4 は閉指令が空振りする試験なので指令は送られ、`no_lift` で終わり物体は持ち上がらない（「持ち上げ最大 z」が机上の {reg.object_rest_z("red_cube"):.3f} m 付近）。

| 試験 | 設定 | 結果 | 理由 | 送った指令数 | 持ち上げ最大 z m | submit 時の物体位置 xy m | 状態遷移 | リセット判定（後） |
|---|---|---|---|---|---|---|---|---|
{chr(10).join(srows)}

## 注記
- **home 位置の変更（Issue #9、2026-09-19）**: 起票時の補足は red (0.16, 0) / blue (0.22, 0) だったが、両物体を同時に置くと red の pick 時に可動顎（径方向 = +x 側に開く）が blue に触れて 7.2 mm 動かし、また red (0.16, 0) → box_b は単独でも `not_seated`（解放後 傾き 12.5°、トレイ壁に乗る）だった。home を y = 0 から ±20 mm へずらした位置では 4/4 かつ他物体の移動 0 mm。0b の T1〜T5 は `trials_0b` の `start_xy_m` を使うので影響しない
- **置き余裕（Issue #21、2026-09-20 に修正）**: 閉指令時の立方体は TCP から約 29.6 mm（設計値 23.5 mm）にあり（降下時に固定顎が顎軸方向へ約 6 mm 押す）、かつ置きの向きが径方向に固定されていたため、置き先で立方体が方位角の分だけ回り、offset が壁側へ向く組合せでは 4 隅が内寸 60 mm に収まらなかった（Issue #9 の red (0.16, 0) → box_b、Issue #20 の 0b T2）。Issue #21 で pick は物体の yaw、place はトレイの yaw に顎を合わせ、持ち上げ後に測った立方体位置で置き TCP を補正した。修正前後の余裕は `docs/evidence/0b/place-margin.md`、設計は `docs/place-margin.md`
- 簡略把持は把持力の証拠にならない（demo-plan）。開発用 CLI の承認フラグは本番の承認迂回に使わない: 承認画面ができたら `approve_from_review` だけが本番経路になる
- テスト: `tests/test_runner.py`（4 組合せ・拒否 4 種・承認迂回なし・停止・再投入・リセット判定）
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default="sim-v1-simple", choices=CONFIG_IDS)
    ap.add_argument("--out", type=Path, default=Path("docs/evidence/4a"))
    ap.add_argument("--label", default="参考")
    ap.add_argument("--video", action="store_true", help="record <fixture>-<config>.mp4 (needs GL)")
    ap.add_argument(
        "--approve-fixed-fixture",
        action="store_true",
        help="mark the development fixtures as approved (the only development approval path)",
    )
    args = ap.parse_args(argv)
    if not args.approve_fixed_fixture:
        print(
            "refusing to run: the 4a fixtures are development input and need "
            "--approve-fixed-fixture to be marked approved (docs/execution-runner.md)",
            file=sys.stderr,
        )
        return 2
    today = dt.date.today().isoformat()
    args.out.mkdir(parents=True, exist_ok=True)
    fixture_runs, scenarios = run_all(args.config, args.label, args.out, args.video, True)
    (args.out / "runs.md").write_text(
        render(fixture_runs, scenarios, args.config, args.label, today, args.video),
        encoding="utf-8",
    )
    log = {
        "label": args.label,
        "date": today,
        "config_id": args.config,
        "machine": {"node": platform.node(), "gpu": _gpu_name()},
        "fixtures": [r.as_dict() for r in fixture_runs],
        "scenarios": [{"name": n, "setup": s, **r.as_dict()} for n, s, r in scenarios],
    }
    (args.out / "runs.json").write_text(
        json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    n_ok = sum(r.outcome == "success" for r in fixture_runs)
    print(f"{args.config}: {n_ok}/4 fixtures success")
    for n, _, r in scenarios:
        print(f"  {n}: {r.outcome} (commands {r.commands_sent})")
    print(f"wrote {args.out / 'runs.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
