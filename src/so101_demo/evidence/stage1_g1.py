"""Run the fixed G1 manifest and write docs/evidence/g1/trials.md (+ trials.json).

    uv run python -m so101_demo.evidence.stage1_g1 --approve-fixed-fixture --label 参考
    MUJOCO_GL=egl uv run python -m so101_demo.evidence.stage1_g1 --approve-fixed-fixture --label 受け入れ --video

Without `--approve-fixed-fixture` nothing runs: the plans are the 4a development fixtures
and the flag is the only development path that marks them approved (docs/execution-runner.md).
Per trial, in manifest order: reset (the runner's, checked) -> stage the objects at the
trial's start positions and check them (`PlanRunner.stage`) -> submit the approved fixed
JSON through `PlanRunner.submit` -> judgement (execution.criteria, unchanged since 0b)
-> reset check. A trial counts as success only when the judge says success (carry audit
included), the start check passed and the reset after the run passed. Failed trials stay
in the log. A laptop / headless run is labelled 参考; the acceptance run on the GPU
machine passes `--label 受け入れ --video` and records `T{nn}-<fixture>-<config>.mp4` per
trial with the screen recorder (1920x1080, 25 fps, labels, NVENC when present).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import platform
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .gl_env import gl_renderer_name, gpu_name, prefer_gpu_on_wsl2

prefer_gpu_on_wsl2()  # before MuJoCo creates a GL context (Issue #10)

import mujoco  # noqa: E402

from ..execution import criteria as C  # noqa: E402
from ..execution.runner import (  # noqa: E402
    PlanRunner,
    ResetCheck,
    RunRecord,
    _now,
    approve_for_development,
)
from ..plan import G1Manifest, G1Trial, Registry, load_g1_manifest, load_registry  # noqa: E402
from ..plan.manifest import MIN_CENTRE_DISTANCE_M, check_manifest  # noqa: E402
from ..plan.paths import G1_MANIFEST_PATH  # noqa: E402
from ..sim.scene import CONFIG_IDS  # noqa: E402
from .overlay import LabelOverlay  # noqa: E402
from .record import VIDEO_FPS, VIDEO_HEIGHT, VIDEO_WIDTH, VideoRecorder  # noqa: E402

OUT_DIR = Path("docs/evidence/g1")
X264_PRESET = "veryfast"  # libx264 fallback only; NVENC is used when ffmpeg has it
GL_INFO: dict[str, str] = {}  # GL_RENDERER and encoder, read while the first renderer is alive


@dataclass
class TrialRecord:
    trial: G1Trial
    start_check: ResetCheck
    run: RunRecord
    machine: str
    label: str

    @property
    def success(self) -> bool:
        return (
            self.run.outcome == "success"
            and self.start_check.passed
            and self.run.reset_after is not None
            and self.run.reset_after.passed
        )

    @property
    def result(self) -> str:
        if self.success:
            return "success"
        if self.run.outcome == "success" and not self.start_check.passed:
            return "start_check_failed"
        return self.run.outcome

    def as_dict(self) -> dict:
        t = self.trial
        return {
            "trial": t.trial,
            "fixture_id": t.fixture,
            "target": t.target,
            "destination": t.destination,
            "start_xy_m": {k: list(v) for k, v in t.start_xy_m.items()},
            "start_check": self.start_check.as_dict(),
            "result": self.result,
            "success": self.success,
            "label": self.label,
            "machine": self.machine,
            **self.run.as_dict(),
        }


def approved(trial: G1Trial, flag: bool):
    return approve_for_development(trial.plan(), trial.fixture, approve_fixed_fixture=flag)


def run_trial(
    runner: PlanRunner,
    manifest: G1Manifest,
    trial: G1Trial,
    flag: bool,
    *,
    recorder: VideoRecorder | None = None,
    rec_holder: dict | None = None,
) -> tuple[ResetCheck, RunRecord]:
    """One manifest trial on an idle runner: stage -> submit. Returns the start check
    and the run record. A trial whose start check fails is not submitted (it would not
    be the trial the manifest describes): its record has outcome `start_check_failed`,
    no commands and no verdict. The recorder, when given, must come with the holder the
    runner's observer reads from; it is active during the run only."""
    if recorder is not None and rec_holder is None:
        raise ValueError("a recorder needs the rec_holder the runner's observer reads")
    reg = runner.registry
    starts = {oid: manifest.start_xyz(trial, oid, reg) for oid in reg.objects}
    start_check = runner.stage(starts)
    if not start_check.passed:
        if recorder is not None:
            recorder.close()
        rec = RunRecord(
            _now(),
            trial.fixture,
            None,
            None,
            runner.config_id,
            "start_check_failed",
            "; ".join(start_check.reasons),
            0,
            [],
            None,
            runner.last_reset,
            runner.reset(),  # back to a known scene for the next trial
            {o: [float(c) for c in runner.gt.pose_of(o).position_m] for o in reg.objects},
            {},
            0.0,
            0.0,
        )
        runner.last_reset = rec.reset_after
        return start_check, rec
    if recorder is not None:
        rec_holder["r"] = recorder
    try:
        rec = runner.submit(approved(trial, flag))
    finally:
        size = rec_holder.pop("r").close() if rec_holder and "r" in rec_holder else None
    if recorder is not None:
        rec.video_path, rec.video_bytes = recorder.path.name, size
    return start_check, rec


def run_manifest(
    manifest: G1Manifest, label: str, out: Path, video: bool, flag: bool
) -> list[TrialRecord]:
    rec_holder: dict[str, VideoRecorder] = {}

    def observer(sim):
        def cb(s):
            if "r" in rec_holder:
                rec_holder["r"](s)

        return cb

    runner = PlanRunner(manifest.config_id, observer=observer if video else None, styled=video)
    reg = runner.registry
    renderer = mujoco.Renderer(runner.sim.model, VIDEO_HEIGHT, VIDEO_WIDTH) if video else None
    if video:
        GL_INFO.setdefault("name", gl_renderer_name())  # read while the context is alive
    machine = _machine()
    records: list[TrialRecord] = []
    try:
        for t in manifest.trials:
            recorder = None
            if video:
                recorder = VideoRecorder(
                    runner.sim,
                    out / f"{t.trial}-{t.fixture}-{manifest.config_id}.mp4",
                    width=VIDEO_WIDTH,
                    height=VIDEO_HEIGHT,
                    fps=VIDEO_FPS,
                    decorate=LabelOverlay(reg, VIDEO_WIDTH, VIDEO_HEIGHT),
                    brighten_headlight=False,
                    preset=X264_PRESET,
                    renderer=renderer,
                    encoder="auto",
                )
                GL_INFO.setdefault("encoder", recorder.encoder)
            start_check, rec = run_trial(
                runner, manifest, t, flag, recorder=recorder, rec_holder=rec_holder
            )
            records.append(TrialRecord(t, start_check, rec, machine, label))
            print(f"{t.trial} {t.fixture}: {records[-1].result}")
    finally:
        if renderer is not None:
            renderer.close()
    return records


def _machine() -> str:
    gpu = gpu_name()
    return f"{platform.node()}、{platform.machine()}、{gpu or 'NVIDIA GPU なし'}"


def _states(r: RunRecord) -> str:
    return " → ".join(f"{t.state.value}" for t in r.transitions) or "-"


def _check(rc: ResetCheck | None) -> str:
    if rc is None:
        return "-"
    pos = max(rc.object_pos_err_m.values()) * 1000 if rc.object_pos_err_m else 0.0
    tilt = max(rc.object_tilt_deg.values()) if rc.object_tilt_deg else 0.0
    return (
        f"{'合' if rc.passed else '否'} (物体 {pos:.2f} mm / {tilt:.2f}°, 関節 {rc.joint_err_deg:.2f}°, "
        f"開 {rc.gripper:.0f}, 拘束 {rc.constraints_active})"
    )


def _xy(xy) -> str:
    return f"({xy[0]:.2f}, {xy[1]:.2f})"


def render(
    records: list[TrialRecord],
    manifest: G1Manifest,
    reg: Registry,
    label,
    today,
    video,
    manifest_commit: str | None,
) -> str:
    n_ok = sum(r.success for r in records)
    n = len(records)
    rows = []
    for tr in records:
        r, t, v = tr.run, tr.trial, tr.run.verdict
        starts = ", ".join(f"{reg.objects[o].label} {_xy(t.start_xy_m[o])}" for o in reg.objects)
        if v is None:  # rejected before any motion
            rows.append(
                f"| {t.trial} | {t.fixture} | {t.target} → {t.destination} | {starts} | {_check(tr.start_check)} "
                f"| **{tr.result}** | - | - | - | - | - | {r.commands_sent} | {_states(r)} | {_check(r.reset_previous)} | {_check(r.reset_after)} "
                f"| - | - | {r.wall_time_s:.1f} | {(r.reason or '')[:80]} |"
            )
            continue
        disp = max(r.other_objects_max_displacement_m.values()) * 1000
        note = []
        if r.reason:
            note.append(str(r.reason)[:80])
        if r.observer_error:
            note.append("録画失敗: " + r.observer_error)
        rows.append(
            f"| {t.trial} | {t.fixture} | {t.target} → {t.destination} | {starts} | {_check(tr.start_check)} "
            f"| **{tr.result}** | {v.max_lift_z:.4f} | {'-' if v.final_center is None else _xy(v.final_center)} "
            f"| {'-' if v.final_tilt_deg is None else f'{v.final_tilt_deg:.2f}'} "
            f"| {v.details.get('carry_max_tcp_distance_m', 0) * 1000:.1f} / {v.details.get('carry_max_zone_excursion_m', 0) * 1000:.1f} / {v.details.get('release_samples_audited', '-')} "
            f"| {disp:.2f} | {r.commands_sent} | {_states(r)} | {_check(r.reset_previous)} | {_check(r.reset_after)} "
            f"| {'-' if r.video_path is None else f'`{r.video_path}` {r.video_bytes / 1e6:.1f} MB'} | {r.sim_time_s:.1f} | {r.wall_time_s:.1f} | {'; '.join(note)} |"
        )
    machine = records[0].machine if records else _machine()
    homes = ", ".join(f"{k} {tuple(v.home_xyz_m[:2])}" for k, v in reg.objects.items())
    trays = ", ".join(f"{k} {_xy(v.center_xy_m)}" for k, v in reg.destinations.items())
    if label == "参考":
        position = "**参考実行**（受け入れ実行ではない）。描画を使わない headless 実行。判定材料は GPU 機の受け入れ実行（`--label 受け入れ --video`）"
    else:
        position = f"**受け入れ実行**（GPU 機: {machine}）。" + (
            f"各試行を固定カメラから {VIDEO_WIDTH}×{VIDEO_HEIGHT}・{VIDEO_FPS} fps・ラベル付きで録画し「動画」列に記す（screen recorder、`MUJOCO_GL=egl`、GL `{GL_INFO.get('name', '?')}`、encoder `{GL_INFO.get('encoder', '?')}`）"
            if video
            else "動画なし"
        )
    cmd = f"{'MUJOCO_GL=egl ' if video else ''}uv run python -m so101_demo.evidence.stage1_g1 --approve-fixed-fixture --label {label}{' --video' if video else ''}"
    commit = (
        f"`{manifest_commit}`"
        if manifest_commit
        else "（未凍結: 作業ツリーの manifest。凍結時に #13 へ commit ハッシュを記録）"
    )
    failures = [r for r in records if not r.success]
    fail_lines = (
        "\n".join(
            f"- {r.trial.trial} {r.trial.fixture}: **{r.result}** —— {r.run.reason or (r.start_check.reasons if not r.start_check.passed else '-')}"
            for r in failures
        )
        or "- なし"
    )
    return f"""# G1 固定 10 試行の実行ログ（{label}）

- 記録日: {today}。生成: `{cmd}`（再現可能）
- 結論: manifest `contracts/fixtures/g1/manifest.json`（commit {commit}）の 10 試行 —— `{manifest.config_id}` **{n_ok}/{n} 成功**。合否の宣言はしない（合格数字と合否はユーザーが #13 で決める）
- 位置づけ: {position}
- 試行の設計と開始位置の決め方（ユーザーが配置を決め、規則と headless 実行で確認）: `docs/g1-manifest.md`。判定は 0b / 4a と同じ `execution/criteria.py`（変更なし）
- 成功 = 判定器 success（搬送監査 A/B/C 含む）**かつ開始配置の判定 合 かつ実行後のリセット判定 合**。失敗した試行も消さない
- 入口は `so101_demo.execution.runner.PlanRunner.submit` の 1 つ。承認マークは `--approve-fixed-fixture`（開発用）のみ。試行の前に `PlanRunner.stage` で両物体を manifest の開始位置へ置き、{C.RESET_SETTLE_S} s 静定してリセットと同じ許容値で配置を判定する（「開始配置」列）

## 共通条件
| 項目 | 値 |
|---|---|
| 機材 | {machine}、Python {platform.python_version()}、MuJoCo {mujoco.__version__} |
| 構成 | `{manifest.config_id}`（簡略把持） |
| シーン | 登録物体 2 個を同時に配置。トレイ: {trays}。home: {homes}（リセット先。開始位置は試行ごとに両物体とも manifest が指定し、home とは限らない。両物体の中心間距離 ≥ {MIN_CENTRE_DISTANCE_M * 1000:.0f} mm） |
| リセット | 各試行の後（と最初）に `PlanRunner.reset`: `mj_resetData` → ホーム姿勢・開・weld 全解除 → 両物体を home へ → {C.RESET_SETTLE_S} s 静定 → 判定: 物体位置差 ≤ {C.RESET_OBJECT_POS_TOL_M * 1000:.0f} mm・傾き ≤ {C.RESET_OBJECT_TILT_MAX_DEG:.0f}°、関節 ≤ {C.RESET_JOINT_TOL_DEG:.0f}°、開 ≥ {C.GRIPPER_OPEN_MIN:.0f}、保持なし、有効拘束 0、1 mm 超の貫入なし |
| 判定値 | 0b と同一: 持ち上げ 中心 z ≥ {C.LIFT_CENTER_Z_M} m を {C.LIFT_HOLD_S} s、着座 底面 4 隅が内寸内・中心 z {C.SEAT_CENTER_Z_M} ± {C.SEAT_Z_TOL_M} m・傾き ≤ {C.TILT_MAX_DEG}°、静止 v < {C.SETTLE_LIN_V_MAX_M_S * 1000:.0f} mm/s かつ ω < {C.SETTLE_ANG_V_MAX_RAD_S} rad/s を {C.SETTLE_HOLD_S} s、時間上限 {C.TIMEOUT_S:.0f} s、搬送監査 TCP 距離 ≤ {C.CARRY_TCP_DIST_MAX_M * 1000:.0f} mm・把持ゾーン内・解放後拘束 0 |
| 正本 | manifest: `contracts/fixtures/g1/manifest.json`。plan: `contracts/fixtures/4a/`。物体・置き先・home: `contracts/registry.json`。判定値: `src/so101_demo/execution/criteria.py`。実行器: `src/so101_demo/execution/runner.py` |

## 10 試行（`{manifest.config_id}`、{label}）: **{n_ok}/{n} 成功**
manifest の順に実行し、毎回リセット。「他物体の移動」= 対象でない物体が実行中に開始位置から動いた最大距離。「結果」は総合結果（`start_check_failed` = 開始配置が不合格（submit しない。指令 0）、`reset_failed` = 判定器は success だが実行後のリセットが不合格）。

| 試行 | fixture | 対象 → 置き先 | 開始位置 xy m | 開始配置 | 結果 | 持ち上げ最大 z m | 解放後 中心 xy m | 傾き ° | 監査: TCP 距離最大 mm / ゾーン逸脱 mm / 解放後監査 tick | 他物体の移動 mm | 送った指令数 | 状態遷移 | リセット判定（前回リセット） | リセット判定（後） | 動画 | 実行 sim 時間 s | 壁時計 s | 備考 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
{chr(10).join(rows)}

## 失敗した試行と理由
{fail_lines}

## 注記
- この記録は「凍結した固定 10 試行を、承認済みと明示した開発用固定 JSON から sim が実行した」証拠である。合格数字（提案値 10/10）はユーザーが #13 で評価前に固定し、この記録の結果に合わせない
- 簡略把持は把持力の証拠にならない（demo-plan 共通規約）。sim の成功は実機の証拠にしない
- テスト: `tests/test_g1_manifest.py`（manifest の規則 (a)〜(e)、開始配置と記録項目）
"""


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=True, timeout=10
    ).stdout.strip()


def _git_commit_of(path: Path, main_ref: str = "main") -> str | None:
    """Full hash of the last commit that changed `path`, but only when that commit is on
    `main` (a task-branch commit disappears in the squash merge) and the file is not
    modified in the working tree. Otherwise None: the manifest is not frozen yet."""
    try:
        if _git("status", "--porcelain", "--", str(path)):
            return None
        commit = _git("log", "-n", "1", "--format=%H", "--", str(path))
        if not commit:
            return None
        on_main = subprocess.run(
            ["git", "merge-base", "--is-ancestor", commit, main_ref],
            capture_output=True,
            timeout=10,
        )
        return commit if on_main.returncode == 0 else None
    except (subprocess.SubprocessError, OSError):
        return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--manifest", type=Path, default=G1_MANIFEST_PATH)
    ap.add_argument("--out", type=Path, default=OUT_DIR)
    ap.add_argument("--label", default="参考", choices=("参考", "受け入れ"))
    ap.add_argument(
        "--video", action="store_true", help="record T<nn>-<fixture>-<config>.mp4 (needs GL)"
    )
    ap.add_argument(
        "--approve-fixed-fixture",
        action="store_true",
        help="mark the development fixtures as approved (the only development approval path)",
    )
    args = ap.parse_args(argv)
    if not args.approve_fixed_fixture:
        print(
            "refusing to run: the G1 plans are the 4a development fixtures and need "
            "--approve-fixed-fixture to be marked approved (docs/execution-runner.md)",
            file=sys.stderr,
        )
        return 2
    manifest = load_g1_manifest(args.manifest)
    if manifest.config_id not in CONFIG_IDS:
        raise SystemExit(f"manifest config_id {manifest.config_id!r} not in {CONFIG_IDS}")
    problems = check_manifest(manifest)
    if problems:
        print("refusing to run: the manifest breaks its rules:", file=sys.stderr)
        for p in problems:
            print("  " + p, file=sys.stderr)
        return 2
    today = dt.date.today().isoformat()
    args.out.mkdir(parents=True, exist_ok=True)
    reg = load_registry()
    records = run_manifest(manifest, args.label, args.out, args.video, args.approve_fixed_fixture)
    commit = _git_commit_of(args.manifest)
    (args.out / "trials.md").write_text(
        render(records, manifest, reg, args.label, today, args.video, commit), encoding="utf-8"
    )
    log = {
        "label": args.label,
        "date": today,
        "config_id": manifest.config_id,
        "manifest": str(args.manifest),
        "manifest_commit": commit,
        "machine": {"node": platform.node(), "gpu": gpu_name()},
        "video": args.video,
        "successes": sum(r.success for r in records),
        "trials": [r.as_dict() for r in records],
    }
    (args.out / "trials.json").write_text(
        json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"{manifest.config_id}: {log['successes']}/{len(records)} trials success")
    print(f"wrote {args.out / 'trials.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
