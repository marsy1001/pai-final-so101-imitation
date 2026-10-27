"""Generate docs/evidence/0b/trials.md (+ trials.json) by running the fixed 0b manifest.

    uv run python -m so101_demo.evidence.stage1_0b [--config ID ...] [--out DIR] [--label 参考]
    MUJOCO_GL=egl uv run python -m so101_demo.evidence.stage1_0b --label 受け入れ --video

Writes only when run as a CLI. Judgement values come from execution.criteria; the
fixture from contracts/registry.json. Default: every known configuration, one
section each. A run on the laptop is labelled 参考 (reference); the acceptance run
happens on the GPU machine with `--video`, one MP4 per trial (`<trial>-<config>.mp4`).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import mujoco
import numpy as np

from ..execution import criteria as C
from ..execution.primitives import (
    CONTROL_DT_S,
    GRIPPER_OPEN_CMD,
    RELEASE_OFFSET_M,
    SEGMENT_S,
    TRANSFER_Z_M,
    GraspParams,
)
from ..execution.trial import HOME_XYZ_M, TrialResult, run_trial
from ..plan import load_registry
from ..sim.grasp import GRASP_ZONE_X_M, GRASP_ZONE_Y_M, GRASP_ZONE_Z_MAX_M, GRASP_ZONE_Z_MIN_M
from ..sim.scene import CONFIG_IDS
from .record import VideoRecorder


def _v(x, nd=4):
    return "(" + ", ".join(f"{float(a):.{nd}f}" for a in x) + ")"


def _fmt_diag(d: dict) -> str:
    def g(k, fmt="{:.1f}"):
        return "-" if k not in d else fmt.format(d[k])

    return (
        f"{g('contacts_at_close', '{}')} / {g('max_penetration_at_close_mm', '{:.2f}')} / {g('jaw_value_at_close')} "
        f"/ {g('slip_while_carried_mm')} / {g('min_contacts_while_carried', '{}')}"
    )


def _fmt_audit(d: dict) -> str:
    """Carry audit (固定 3/3): max TCP distance, max zone excursion, true release and
    its lead over the trace label, constraints active after the release."""

    def mm(k):
        return "-" if d.get(k) is None else f"{d[k] * 1000:.1f}"

    if d.get("release_open_s") is None:
        rel = "-"
    else:
        rel = f"{d['release_open_s']:.2f}"
        if d.get("release_trace_s") is not None:
            rel += f" ({d['release_open_s'] - d['release_trace_s']:+.2f})"
    if d.get("release_samples_audited"):
        eq = f"0 ({d['release_samples_audited']} tick)"
    elif d.get("constraints_active") is not None:
        eq = str(d["constraints_active"])
    else:
        eq = "-"
    zone = mm("carry_max_zone_excursion_m")
    if d.get("carry_samples_audited"):
        zone += f" ({d['carry_samples_audited']} tick)"
    return f"{mm('carry_max_tcp_distance_m')} / {zone} / {rel} / {eq}"


def _fmt_video(r: TrialResult) -> str:
    if r.video_path is None:
        return "-"
    return f"`{Path(r.video_path).name}` {r.video_bytes / 1e6:.1f} MB"


def _gpu_name() -> str | None:
    if shutil.which("nvidia-smi") is None:
        return None
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return None
    return out.splitlines()[0] if out else None


def render_config(results: list[TrialResult], config_id: str, label: str, today: str) -> str:
    reg = load_registry()
    gp = GraspParams.for_config(config_id)
    n_ok = sum(r.verdict.success for r in results)
    rows = []
    for r in results:
        v, s = r.verdict, r.spec
        rows.append(
            f"| {s.trial} | {config_id} | {s.object} | {_v(reg.start_xyz(s), 3)} | {s.destination} "
            f"| {'success' if v.success else v.failure} | {v.max_lift_z:.4f} "
            f"| {'-' if v.final_center is None else _v(v.final_center[:2])} | {'-' if v.final_center is None else f'{v.final_center[2]:.4f}'} "
            f"| {'-' if v.final_tilt_deg is None else f'{v.final_tilt_deg:.2f}'} "
            f"| {'-' if v.settle_time is None or v.release_time is None else f'{v.settle_time - v.release_time:.2f}'} "
            f"| {_fmt_diag(r.diagnostics)} | {_fmt_audit(v.details)} | {_fmt_video(r)} "
            f"| {r.sim_time_s:.1f} | {r.wall_time_s:.1f} | {today} |"
        )
    home_q = results[0].home_q_deg if results else np.zeros(5)
    if config_id == "sim-v1-simple":
        grasp_desc = (
            f"簡略把持（閉指令時に把持領域内の物体がちょうど 1 個なら weld で保持。領域: site 座標で X ±{GRASP_ZONE_X_M * 1000:.0f} mm、"
            f"Y ±{GRASP_ZONE_Y_M * 1000:.0f} mm、Z [{GRASP_ZONE_Z_MIN_M * 1000:+.0f}, {GRASP_ZONE_Z_MAX_M * 1000:+.0f}] mm）。"
            f"weld solref (0.005, 1) solimp (0.99, 0.99, 0.001)。閉指令 {gp.close_cmd:.0f}、顎ポケット中央に隙間 {gp.pocket_clearance_m * 1000:.1f} mm"
        )
        note = "簡略把持は把持力の証拠にならない（demo-plan）。"
    else:
        grasp_desc = (
            f"接触把持（モデルに weld なし。閉指令 {gp.close_cmd:.0f} = 全閉目標で position actuator が力上限まで挟む。"
            f"立方体の面を固定顎パッドに当てる: 隙間 {gp.pocket_clearance_m * 1000:.1f} mm）。接触設定は MJCF 既定のまま（solref 0.02 s / solimp 既定 / condim 3 / cone pyramidal / impratio 1）"
        )
        note = "ベースライン構成（接触設定の変更 0 回）。"
    max_pen = max(
        (r.diagnostics.get("max_penetration_at_close_mm", 0.0) for r in results), default=0.0
    )
    return f"""## 構成 `{config_id}`（{label}）: **{n_ok}/5 成功**

| 項目 | 値 |
|---|---|
| 把持方式 | {grasp_desc} |
| 把持点 | pick: TCP = 立方体中心 − (半辺 20 + 隙間) mm × 顎開き方向（TCP は固定顎の内面にあり、可動顎が site +Z 側から閉じる。実測 2026-09-19）。顎の向きは物体の登録 yaw（0 = +x）。z は机面 + 半辺（既知値。位置取得からは x, y のみ）。place: 顎の向きはトレイの軸（yaw 0）、TCP = トレイ中心 − 持ち上げ後に測った立方体の顎軸上の位置（設計 23.5 mm、実測約 29.6 mm）× 顎開き方向（Issue #21、2026-09-20。`docs/place-margin.md`） |
| 動作 | 制御 {1 / CONTROL_DT_S:.0f} Hz、直線区間 {SEGMENT_S} s、搬送高さ TCP z {TRANSFER_Z_M} m、解放高さ 着座 + {RELEASE_OFFSET_M * 1000:.0f} mm、開指令 {GRIPPER_OPEN_CMD:.0f} |
| ホーム | TCP {HOME_XYZ_M} m の真上把持姿勢 = 関節 {_v(home_q, 1)} deg |

| trial | 設定 ID | 物体 | 初期位置 m | 置き先 | 結果 | 持ち上げ最大 z m | 解放後 中心 xy m | 中心 z m | 傾き ° | 静止まで s | 診断: 閉時接触数 / 閉時最大貫入 mm / 閉時顎値 / 搬送中滑り mm / 搬送中最小接触数 | 監査: 搬送中 TCP 距離最大 mm / ゾーン逸脱最大 mm / 真の解放 s (trace との差) / 解放後の有効拘束数 | 動画 | 試行 sim 時間 s | 壁時計 s | 日時 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
{chr(10).join(rows)}

{note} 初期 pose の干渉検査（1 mm 超の貫入なし）: {"すべて 0 件" if all(not r.initial_penetrations for r in results) else "貫入あり（要確認）"}。閉時の最大貫入は {max_pen:.2f} mm。
"""


def render(all_results: dict[str, list[TrialResult]], label: str, today: str) -> str:
    summary = "、".join(
        f"`{cid}` {sum(r.verdict.success for r in rs)}/5" for cid, rs in all_results.items()
    )
    sections = "\n".join(render_config(rs, cid, label, today) for cid, rs in all_results.items())
    gpu = _gpu_name()
    machine = f"{platform.node()}、{platform.machine()}、{gpu or 'NVIDIA GPU なし'}"
    with_video = any(r.video_path for rs in all_results.values() for r in rs)
    if label == "参考":
        position = (
            "**参考実行**（受け入れ実行ではない）。描画を使わない headless 実行。"
            "受け入れ判定（5/5、動画付き）は母艦で行う（ADR 0006）"
        )
    else:
        position = f"**受け入れ実行**（母艦: {machine}。ADR 0006）。" + (
            "各試行を固定カメラから録画し「動画」列に記す（`so101_demo.evidence.record`、`MUJOCO_GL=egl`）"
            if with_video
            else "動画なし"
        )
    return f"""# 0b: pick → place 試行ログ（{label}）

- 記録日: {today}。生成: `{"MUJOCO_GL=egl " if with_video else ""}uv run python -m so101_demo.evidence.stage1_0b --label {label}{" --video" if with_video else ""}`（再現可能。構成ごとに 1 節）
- 結論: 固定 manifest 5 試行の結果 —— {summary}
- 位置づけ: {position}
- 判定は物体 pose・速度とグリッパー値だけで行う（`so101_demo.execution.judge`）。簡略把持の保持リストは使わない。接触版と簡略版は別構成として記録し、混ぜない
- 成功 = 判定器の success **かつ** 搬送監査 A・B・C の通過（Issue #4 固定 3/3、2026-09-19）。監査値は「監査」列
- 以前の記録は上書きしない: 旧配置（トレイ (0.19, ±0.085)、2026-09-20 ADR 0008 で変更）の記録は `tray19/`: 受け入れ実行 `tray19/trials.md`、XPS の参考実行 `tray19/trials-reference-xps.md` / `.json`（2026-09-19、`sim-v1-simple` 参考 5/5・`sim-v1-contact` 参考 0/5）、接触把持の step 単位診断 `tray19/contact-diagnosis.md`（`3f30fc2`）。新配置で置き余裕を直す前（Issue #20、`7604201`、`sim-v1-simple` 参考 4/5）の記録は `issue20/trials.md` / `.json`

## 共通条件
| 項目 | 値 |
|---|---|
| 機材 | {machine}、Python {platform.python_version()}、MuJoCo {mujoco.__version__} |
| 物理 | timestep 0.002 s（MJCF 既定）、立方体 40 mm・20 g、摩擦 MuJoCo 既定 (1, 0.005, 0.0001)、グリッパー actuator kp 998.22・forcerange ±3.35（MJCF 既定） |
| リセット | `mj_resetData` → ホーム姿勢・グリッパー開・（簡略版は weld 全解除）→ 物体を初期 pose に配置 → {C.RESET_SETTLE_S} s 静止 |
| 判定値 | 持ち上げ 中心 z ≥ {C.LIFT_CENTER_Z_M} m を {C.LIFT_HOLD_S} s、閉 ≤ {C.GRIPPER_CLOSED_MAX:.0f}。着座: 底面 4 隅が内寸内、中心 z {C.SEAT_CENTER_Z_M} ± {C.SEAT_Z_TOL_M} m、傾き ≤ {C.TILT_MAX_DEG}°。静止: v < {C.SETTLE_LIN_V_MAX_M_S * 1000:.0f} mm/s かつ ω < {C.SETTLE_ANG_V_MAX_RAD_S} rad/s を {C.SETTLE_HOLD_S} s、解放後 {C.SETTLE_WINDOW_S} s 以内、開 ≥ {C.GRIPPER_OPEN_MIN:.0f}。時間上限 {C.TIMEOUT_S:.0f} s（sim）。`dropped` = 解放前に TCP からの距離が持ち上げ時 + {C.DROP_SEPARATION_M * 1000:.0f} mm 超（2026-09-19 修正、Issue #4）。deadline 以後に観測した失敗は `timeout` |
| 搬送監査（固定 3/3） | 窓 = 持ち上げ〜真の解放の全制御 tick。A: 立方体中心が tip-site 系の把持ゾーン内（X ±{GRASP_ZONE_X_M * 1000:.0f} mm、Y ±{GRASP_ZONE_Y_M * 1000:.0f} mm、Z [{GRASP_ZONE_Z_MIN_M * 1000:+.0f}, {GRASP_ZONE_Z_MAX_M * 1000:+.0f}] mm）。B: TCP–立方体距離 ≤ {C.CARRY_TCP_DIST_MAX_M * 1000:.0f} mm。C: 真の解放 = 持ち上げ後に**実測**グリッパー値が初めて {C.GRIPPER_OPEN_MIN:.0f} 以上になった tick（trace の `place:released` は開指令の {0.5} s 後で遅い）。`dropped` 窓の終端と静止窓の起点は真の解放。解放以後は有効な equality 拘束 0 本。不成立は `not_contained`（A/B）、`still_constrained`（C）。監査入力（tip-site 系の物体位置、有効拘束数）が窓内のどの tick でも欠けていれば `unaudited`（合格にしない） |
| 診断列 | 閉時接触数 = 閉指令後の顎と立方体の接触点数、閉時最大貫入 = その最大貫入深さ、閉時顎値 = グリッパー値（0〜100）、搬送中滑り = 持ち上げ〜解放の間に立方体が gripper 座標でずれた最大距離、搬送中最小接触数 |
| 監査列 | 搬送中 TCP 距離最大、把持ゾーンからの逸脱最大（0.0 = 常に内側。括弧内は監査した搬送中 tick 数 = 窓の全 tick）、真の解放時刻と trace 値との差、真の解放以後の有効拘束数（0 が合格。括弧内は監査した tick 数）。監査入力が 1 tick でも欠けた試行は `unaudited` で不合格。接触構成では監査の前に `no_lift` で終わることが多く、その場合は `-` |
| 正本 | 物体・置き先・manifest: `contracts/registry.json`。判定値: `src/so101_demo/execution/criteria.py`。動作定数: `src/so101_demo/execution/primitives.py`。構成: `src/so101_demo/sim/scene.py` |

{sections}
## 注記
- **置き余裕（Issue #21、2026-09-20）**: 配置変更（ADR 0008）直後の参考実行では T2（blue (22, 0) → box_b）が `not_seated`（壁に乗って傾き 12.7°）だった（`issue20/trials.md`）。原因は置きの向きが基部から径方向に固定されていたこと（トレイの方位 ±22° の分だけ回って着地）と、降下時に固定顎が立方体を顎軸方向へ約 6 mm 押すこと。pick は物体の yaw、place はトレイの yaw に顎を合わせ、持ち上げ後に測った立方体位置で置き TCP を補正した。修正前後の中心ずれ・yaw 差・着地余裕は `place-margin.md`（固定 9 試行 + 掃引）。受け入れ動画の最終フレーム（立方体の辺がトレイと平行）は `captures/T<n>-sim-v1-simple-final.png`
- 接触把持の投資上限（demo-plan）: 接触設定の変更は一貫した束で 1 回まで、端末をまたいで通算。ベースラインの失敗記録は残す。2026-10-02 までに掴めなければ 2026-10-05 から簡略版を本採用
- 接触設定の変更枠: **未使用（通算 0/1）**。採用構成は `sim-v1-simple`（Issue #6 の方針決定 2026-09-19、ADR 0007 Proposed）。`sim-v1-contact` は同じ manifest・同じ判定で別構成として記録し、失敗の可視化として動画も残す（母艦での可視化。設定は MJCF 既定のまま）
- 簡略把持は把持力の証拠にならない。大画面では常時「シミュレーション」と表示する（`docs/design/contact-grasp-approach.md`）
- テスト: `tests/test_execution_pick_place.py`（同じ manifest を両構成で headless 実行）、`tests/test_judge.py`（判定器の固定入力・監査 A/B/C）、`tests/test_render.py`（`gpu`: 描画と MP4）
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", action="append", choices=CONFIG_IDS, help="default: all")
    ap.add_argument("--out", type=Path, default=Path("docs/evidence/0b"))
    ap.add_argument("--label", default="参考")
    ap.add_argument(
        "--video", action="store_true", help="record <trial>-<config>.mp4 per trial (needs GL)"
    )
    args = ap.parse_args(argv)
    configs = tuple(args.config) if args.config else CONFIG_IDS
    today = dt.date.today().isoformat()
    reg = load_registry()
    args.out.mkdir(parents=True, exist_ok=True)

    def run(t, cid) -> TrialResult:
        if not args.video:
            return run_trial(t, cid, reg)
        path = args.out / f"{t.trial}-{cid}.mp4"
        rec: dict[str, VideoRecorder] = {}

        def observer(sim):
            rec["r"] = VideoRecorder(sim, path)
            return rec["r"]

        try:
            res = run_trial(t, cid, reg, observer=observer)
        finally:
            size = rec["r"].close() if "r" in rec else None
        res.video_path, res.video_bytes = str(path), size
        return res

    all_results = {cid: [run(t, cid) for t in reg.trials_0b] for cid in configs}
    (args.out / "trials.md").write_text(render(all_results, args.label, today), encoding="utf-8")
    log = [
        {
            "trial": r.spec.trial,
            "config_id": r.config_id,
            "object": r.spec.object,
            "start_xyz_m": reg.start_xyz(r.spec).tolist(),
            "destination": r.spec.destination,
            "result": "success" if r.verdict.success else r.verdict.failure,
            "max_lift_z_m": r.verdict.max_lift_z,
            "final_center_m": None
            if r.verdict.final_center is None
            else r.verdict.final_center.tolist(),
            "final_tilt_deg": r.verdict.final_tilt_deg,
            "lift_time_s": r.verdict.lift_time,
            "release_time_s": r.verdict.release_time,
            "settle_time_s": r.verdict.settle_time,
            "sim_time_s": r.sim_time_s,
            "wall_time_s": r.wall_time_s,
            "diagnostics": r.diagnostics,
            "video": None
            if r.video_path is None
            else {"file": Path(r.video_path).name, "bytes": r.video_bytes},
            "machine": {"node": platform.node(), "gpu": _gpu_name()},
            "label": args.label,
            "date": today,
            "details": r.verdict.details,
        }
        for rs in all_results.values()
        for r in rs
    ]
    (args.out / "trials.json").write_text(
        json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for cid, rs in all_results.items():
        print(f"{cid}: {sum(r.verdict.success for r in rs)}/{len(rs)} success")
    print(f"wrote {args.out / 'trials.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
