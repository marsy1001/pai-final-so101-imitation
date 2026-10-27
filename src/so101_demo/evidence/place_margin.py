"""Placing margin of the fixed trials: writes place-margin.md + .json under --out.

    uv run python -m so101_demo.evidence.place_margin                      # after the fix
    uv run python -m so101_demo.evidence.place_margin --baseline --commit <hash>   # before

Headless, `sim-v1-simple`. Three groups, every one through `execution.trial.run_trial`
(the 0b primitives and judge; no plan / approval path, no reset check):
  fixed   0b T1-T5 (trial object alone, as the 0b manifest runs) and 4a F1-F4 (target
          from its registered home, the other cube at its home; the fixture JSON names
          target and destination)
  sweep   every 10 mm grid point of the work area that satisfies the G1 start rules
          (`plan.manifest.start_violations`: dist / tray / tcp, non-target at home)
          x 2 objects x 2 destinations, two cubes in the scene
  column  the y = 0 column, x 0.16..0.23, x 2 objects x 2 destinations, target alone
          (the failure shape of the red (16, 0) trial and the T2 blue (22, 0) trial of
          2026-09-20; most of these
          points break the `dist` rule when the other cube is at home, so they are run
          without it, like the 0b trials)
Per run: result, final centre offset from the tray centre, cube yaw against the tray
axes (folded to +-45 deg), landing margin = the smallest distance from any bottom corner
to the nearest inner wall (negative = outside), final tilt, and the cube's position in
the tip frame after the lift (the grasp offset the place correction compensates).

`--baseline` writes place-margin-before.json only (run it on the code before the fix
and name that commit). The default run writes place-margin.json (before + after) and
renders place-margin.md with both side by side. Acceptance value: MARGIN_MIN_M.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import platform
import subprocess
import sys
from pathlib import Path

import numpy as np

from ..execution.judge import Sample, bottom_corners_xy, tilt_deg
from ..execution.trial import TrialResult, run_trial
from ..kinematics.top_down import WORKSPACE_MAX, WORKSPACE_MIN
from ..plan import DestinationSpec, ObjectSpec, Registry, TrialSpec, load_registry
from ..plan.manifest import start_violations
from ..plan.paths import FIXTURES_4A_DIR
from ..plan.schema import validate_plan
from .gl_env import gpu_name

MARGIN_MIN_M = 0.005  # landing margin every fixed trial must keep (fixed 2026-09-20)
GRID_M = 0.010
COLUMN_XS_M = (0.16, 0.17, 0.18, 0.19, 0.20, 0.21, 0.22, 0.23)  # the y = 0 column
CONFIG_ID = "sim-v1-simple"
OUT_DIR = Path("docs/evidence/0b")
BEFORE_JSON = "place-margin-before.json"
AFTER_JSON = "place-margin.json"
REPORT_MD = "place-margin.md"
# the pick / place path whose commit is recorded as "the code under measurement"
CODE_PATHS = (
    "src/so101_demo/execution",
    "src/so101_demo/kinematics",
    "src/so101_demo/sim",
    "src/so101_demo/plan",
    "src/so101_demo/robot",
    "src/so101_demo/locate",
    "contracts",
)


# --- metrics --------------------------------------------------------------------------
def yaw_vs_axes_deg(rot: np.ndarray) -> float:
    """Yaw of the cube's local x axis against the world axes, folded to (-45, 45]: a cube
    is square, so 0 means its edges are parallel to the (axis-aligned) tray walls."""
    yaw = float(np.degrees(np.arctan2(rot[1, 0], rot[0, 0])))
    return float(((yaw + 45.0) % 90.0) - 45.0)


def landing_margin_m(
    pos: np.ndarray, rot: np.ndarray, obj: ObjectSpec, dest: DestinationSpec
) -> float:
    """Smallest distance from any bottom corner to the nearest inner wall (m); negative
    when a corner is outside the inner square."""
    cx, cy = dest.center_xy_m
    half = dest.inner_m / 2
    corners = bottom_corners_xy(pos, rot, obj.size_m)
    per_corner = np.minimum(half - np.abs(corners[:, 0] - cx), half - np.abs(corners[:, 1] - cy))
    return float(per_corner.min())


def _final_sample(r: TrialResult) -> Sample | None:
    if not r.samples:
        return None
    v = r.verdict
    if v.settle_time is not None:
        return next((s for s in r.samples if s.t >= v.settle_time), r.samples[-1])
    return r.samples[-1]


def measure(r: TrialResult, reg: Registry) -> dict:
    """Metrics of one run (JSON-ready)."""
    obj, dest = reg.objects[r.spec.object], reg.destinations[r.spec.destination]
    v = r.verdict
    out: dict = {
        "trial": r.spec.trial,
        "object": r.spec.object,
        "start_xy_m": list(r.spec.start_xy_m),
        "destination": r.spec.destination,
        "result": "success" if v.success else v.failure,
        "reason": v.details.get("reason"),
        "final_tilt_deg": v.final_tilt_deg,
        "sim_time_s": r.sim_time_s,  # no wall time: the record is deterministic and diffable
        "centre_offset_mm": None,
        "centre_offset_norm_mm": None,
        "yaw_vs_tray_deg": None,
        "margin_mm": None,
        "held_offset_mm": None,
        "other_objects_final_displacement_mm": {
            k: val * 1000
            for k, val in r.diagnostics.get("other_objects_final_displacement_m", {}).items()
        },
    }
    if v.lift_time is not None:
        at_lift = next((s for s in r.samples if s.t >= v.lift_time), None)
        if at_lift is not None:
            out["held_offset_mm"] = [float(c) * 1000 for c in at_lift.obj_in_tip]
    s = _final_sample(r)
    if s is not None and v.lift_time is not None and v.release_time is not None:
        d = s.obj_pos[:2] - np.asarray(dest.center_xy_m)
        out["centre_offset_mm"] = [float(d[0]) * 1000, float(d[1]) * 1000]
        out["centre_offset_norm_mm"] = float(np.linalg.norm(d)) * 1000
        out["yaw_vs_tray_deg"] = yaw_vs_axes_deg(s.obj_rot)
        out["margin_mm"] = landing_margin_m(s.obj_pos, s.obj_rot, obj, dest) * 1000
        out["final_tilt_deg"] = tilt_deg(s.obj_rot)
    return out


def passes(m: dict) -> bool:
    """The acceptance rule of 2026-09-20 for one fixed trial."""
    return (
        m["result"] == "success"
        and m["margin_mm"] is not None
        and (m["margin_mm"] >= MARGIN_MIN_M * 1000 - 1e-9)
    )


# --- trial sets -----------------------------------------------------------------------
def fixture_trials(reg: Registry) -> list[TrialSpec]:
    """F1-F4 as trials: target from its registered home, destination from the fixture."""
    out = []
    for path in sorted(FIXTURES_4A_DIR.glob("*.json")):
        plan = validate_plan(json.loads(path.read_text(encoding="utf-8")))
        home = reg.objects[plan.target].home_xyz_m
        out.append(TrialSpec(path.stem, plan.target, (home[0], home[1]), plan.destination))
    return out


def grid_points() -> list[tuple[float, float]]:
    xs = np.round(np.arange(WORKSPACE_MIN[0], WORKSPACE_MAX[0] + GRID_M / 2, GRID_M), 3)
    ys = np.round(np.arange(WORKSPACE_MIN[1], WORKSPACE_MAX[1] + GRID_M / 2, GRID_M), 3)
    return [(float(x), float(y)) for x in xs for y in ys]


def sweep_trials(reg: Registry) -> list[TrialSpec]:
    """Every grid start that satisfies the G1 rules with the other cube at home, for both
    objects and both destinations. Trial ids: `S-<object>-<x cm>_<y cm>-<dest>`."""
    out = []
    for oid in reg.objects:
        others = {o: reg.objects[o].home_xyz_m[:2] for o in reg.objects if o != oid}
        for xy in grid_points():
            if start_violations(reg, oid, xy, others):
                continue
            for did in reg.destinations:
                out.append(TrialSpec(_sweep_id("S", oid, xy, did), oid, xy, did))
    return out


def column_trials(reg: Registry) -> list[TrialSpec]:
    """The y = 0 column, both objects, both destinations (target alone in the scene)."""
    return [
        TrialSpec(_sweep_id("C", oid, (x, 0.0), did), oid, (x, 0.0), did)
        for oid in reg.objects
        for x in COLUMN_XS_M
        for did in reg.destinations
    ]


def _sweep_id(prefix: str, oid: str, xy, did: str) -> str:
    return f"{prefix}-{oid}-{round(xy[0] * 100):d}_{round(xy[1] * 100):+d}-{did}"


def run_group(trials: list[TrialSpec], reg: Registry, *, two_cubes: bool) -> list[dict]:
    objects = tuple(reg.objects) if two_cubes else None
    out = []
    for t in trials:
        r = run_trial(t, CONFIG_ID, reg, objects=objects)
        m = measure(r, reg)
        out.append(m)
        print(
            f"{t.trial}: {m['result']}"
            + ("" if m["margin_mm"] is None else f" margin {m['margin_mm']:.1f} mm")
        )
    return out


def run_all(reg: Registry, *, sweep: bool = True) -> dict:
    fixed = run_group(list(reg.trials_0b), reg, two_cubes=False) + run_group(
        fixture_trials(reg), reg, two_cubes=True
    )
    out = {"fixed": fixed}
    if sweep:
        out["sweep"] = run_group(sweep_trials(reg), reg, two_cubes=True)
        out["column"] = run_group(column_trials(reg), reg, two_cubes=False)
    return out


# --- rendering ------------------------------------------------------------------------
def _git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], capture_output=True, text=True, check=True, timeout=10
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return "?"


def _cell(m: dict | None) -> str:
    if m is None:
        return "- | - | - | - | -"
    if m["margin_mm"] is None:
        return f"{m['result']} | - | - | - | -"
    dx, dy = m["centre_offset_mm"]
    return (
        f"{m['result']} | ({dx:+.1f}, {dy:+.1f}) {m['centre_offset_norm_mm']:.1f} "
        f"| {m['yaw_vs_tray_deg']:+.1f} | **{m['margin_mm']:.1f}** | {m['final_tilt_deg']:.1f}"
    )


def _index(ms: list[dict]) -> dict[str, dict]:
    return {m["trial"]: m for m in ms}


def _summary(ms: list[dict]) -> tuple[int, int, float | None]:
    ok = sum(m["result"] == "success" for m in ms)
    margins = [m["margin_mm"] for m in ms if m["margin_mm"] is not None]
    return ok, len(ms), (min(margins) if margins else None)


def _failures(ms: list[dict]) -> str:
    bad = [m for m in ms if m["result"] != "success"]
    if not bad:
        return "なし"
    return "、".join(
        f"`{m['trial']}` {m['result']}"
        + ("" if m["margin_mm"] is None else f"（余裕 {m['margin_mm']:.1f} mm）")
        + ("" if not m.get("reason") else f"（{str(m['reason'])[:60]}）")
        for m in bad
    )


def _mm(v) -> str:
    return "-" if v is None else f"{v:.1f}"


def render(before: dict | None, after: dict, reg: Registry, today: str) -> str:
    fixed_a = after["fixed"]
    ok_a, n_a, min_a = _summary(fixed_a)
    all_pass = all(passes(m) for m in fixed_a)
    lines = [
        "# 置き余裕（着地余裕）の記録: 修正前と修正後",
        "",
        f"- 記録日: {today}。生成: `uv run python -m so101_demo.evidence.place_margin`（再現可能。修正前の半分は `--baseline --commit <hash>` で先に作った `{BEFORE_JSON}` から読む）。機材: {platform.node()}、{gpu_name() or 'NVIDIA GPU なし'}、headless、構成 `{CONFIG_ID}`",
    ]
    if before is not None:
        ok_b, n_b, min_b = _summary(before["fixed"])
        lines.append(
            f"- 結論: 固定 9 試行（0b T1〜T5、4a F1〜F4 home から）は 修正前 **{ok_b}/{n_b} success、着地余裕最小 {_mm(min_b)} mm** → 修正後 **{ok_a}/{n_a} success、着地余裕最小 {_mm(min_a)} mm**（受け入れ値 ≥ {MARGIN_MIN_M * 1000:.0f} mm: {'**全試行 合格**' if all_pass else '**不合格あり**'}）"
        )
    else:
        lines.append(
            f"- 結論: 固定 9 試行は **{ok_a}/{n_a} success、着地余裕最小 {_mm(min_a)} mm**（受け入れ値 ≥ {MARGIN_MIN_M * 1000:.0f} mm: {'合格' if all_pass else '不合格あり'}）。修正前の記録（`{BEFORE_JSON}`）が無いので比較列は空"
        )
    if "column" in after:
        okc, nc, _ = _summary(after["column"])
        lines.append(
            f"- y = 0 の列（x 16〜23 cm、両物体 × box_a / box_b、対象のみ配置）: 修正後 **{okc}/{nc} success**（失敗: {_failures(after['column'])}）"
            + (
                f"。修正前 {_summary(before['column'])[0]}/{_summary(before['column'])[1]}（失敗: {_failures(before['column'])}）"
                if before is not None and "column" in before
                else ""
            )
        )
    if "sweep" in after:
        oks, ns, mins = _summary(after["sweep"])
        lines.append(
            f"- 掃引（G1 の規則を満たす 10 mm 格子の開始点 × 2 物体 × 2 置き先、非対象は home）: 修正後 **{oks}/{ns} success**、余裕最小 {_mm(mins)} mm（失敗: {_failures(after['sweep'])}）"
            + (
                f"。修正前 {_summary(before['sweep'])[0]}/{_summary(before['sweep'])[1]}、余裕最小 {_mm(_summary(before['sweep'])[2])} mm（失敗: {_failures(before['sweep'])}）"
                if before is not None and "sweep" in before
                else ""
            )
            + "。掃引全体の合格数字は決めない（記録のみ）"
        )
    lines += [
        f"- 修正前 = `main` {before['commit'] if before else '-'}（{before['date'] if before else '-'}、置きの向き = 基部から径方向、置き TCP = 設計値）。修正後 = `{after['commit']}`（pick / place の経路を最後に変えた commit。置きの yaw をトレイに合わせ、持ち上げ後に測った把持オフセットで置き TCP を補正。設計は `docs/place-margin.md`）",
        "- 判定は `execution.judge`（`criteria.py` の固定値。変更なし）。列の意味: 中心ずれ = 静止時の立方体中心 − トレイ中心 (dx, dy) とその大きさ (mm)。yaw 差 = 立方体の辺とトレイ軸の角度差（±45° に折り返し）。**着地余裕** = 底面 4 隅それぞれから最寄りの内壁までの距離の最小値（負 = 内寸の外）。傾き = 静止時。把持オフセット = 持ち上げ時の立方体中心の tip-site 座標 (X 接近, Y, Z 顎開き) mm、設計値 (0, 0, 23.5)",
        "- 4a F1〜F4 は fixture JSON の対象・置き先を、対象 home から、非対象を home に置いて 0b と同じ試行実行器で回した（実行器 `PlanRunner` の承認・リセット判定は通さない）",
        "",
        "## 固定 9 試行: 修正前 → 修正後",
        "",
        "| 試行 | 物体 | 開始 xy m | 置き先 | 修正前 結果 | 中心ずれ mm | yaw 差 ° | 余裕 mm | 傾き ° | 修正後 結果 | 中心ずれ mm | yaw 差 ° | 余裕 mm | 傾き ° | 修正後 把持オフセット mm |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    bidx = _index(before["fixed"]) if before else {}
    for m in fixed_a:
        held = m["held_offset_mm"]
        held_s = "-" if held is None else f"({held[0]:.1f}, {held[1]:.1f}, {held[2]:.1f})"
        lines.append(
            f"| {m['trial']} | {m['object']} | ({m['start_xy_m'][0]:.2f}, {m['start_xy_m'][1]:.2f}) | {m['destination']} "
            f"| {_cell(bidx.get(m['trial']))} | {_cell(m)} | {held_s} |"
        )
    lines.append("")
    lines.append(
        f"受け入れ（2026-09-20 に固定）: 修正後の 9 試行すべて success かつ "
        f"余裕 ≥ {MARGIN_MIN_M * 1000:.0f} mm → {'**合格**' if all_pass else '**不合格**'}。"
        f"テスト `tests/test_execution_pick_place.py` が 0b T1〜T5 で同じ値を確認する。"
    )
    if "column" in after:
        lines += [
            "",
            "## y = 0 の列（x 16〜23 cm、対象のみ配置）: 修正前 → 修正後",
            "",
            "2026-09-20 以前の試行 red (16, 0) → box_b と T2 blue (22, 0) → box_b がこの型。"
            "非対象を home に置くと `dist` 規則（中心間 ≥ 60 mm）で大半が落ちるので、"
            "この列は 0b と同じく対象だけを置いて回す。",
            "",
            "| 物体 | 開始 x m | 置き先 | 修正前 結果 | 中心ずれ mm | yaw 差 ° | 余裕 mm | 傾き ° | 修正後 結果 | 中心ずれ mm | yaw 差 ° | 余裕 mm | 傾き ° |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        cidx = _index(before["column"]) if before and "column" in before else {}
        for m in after["column"]:
            lines.append(
                f"| {m['object']} | {m['start_xy_m'][0]:.2f} | {m['destination']} | {_cell(cidx.get(m['trial']))} | {_cell(m)} |"
            )
    if "sweep" in after:
        pts = {(m["object"], tuple(m["start_xy_m"])) for m in after["sweep"]}
        lines += [
            "",
            "## 掃引: G1 の規則を満たす開始点 × 2 置き先（非対象は home、両立方体を配置）",
            "",
            f"開始点の規則は `plan.manifest.start_violations`（`dist` 中心間 ≥ 60 mm、`tray` トレイ外形と重ならない、`tcp` 把持 TCP が作業領域内。作業領域 x {WORKSPACE_MIN[0]:.2f}〜{WORKSPACE_MAX[0]:.2f}、y {WORKSPACE_MIN[1]:.2f}〜{WORKSPACE_MAX[1]:.2f} の {GRID_M * 1000:.0f} mm 格子）。開始点 {len(pts)} 点（red {sum(1 for o, _ in pts if o == 'red_cube')}、blue {sum(1 for o, _ in pts if o == 'blue_cube')}）× 2 置き先 = {len(after['sweep'])} run。合格数字は決めない。",
            "",
        ]
        sidx = _index(before["sweep"]) if before and "sweep" in before else {}
        for did in reg.destinations:
            lines += [
                f"### 置き先 {did}",
                "",
                "| 物体 | 開始 xy m | 修正前 結果 | 中心ずれ mm | yaw 差 ° | 余裕 mm | 傾き ° | 修正後 結果 | 中心ずれ mm | yaw 差 ° | 余裕 mm | 傾き ° | 非対象の移動 mm |",
                "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
            ]
            for m in after["sweep"]:
                if m["destination"] != did:
                    continue
                disp = max(m["other_objects_final_displacement_mm"].values(), default=0.0)
                lines.append(
                    f"| {m['object']} | ({m['start_xy_m'][0]:.2f}, {m['start_xy_m'][1]:.2f}) | {_cell(sidx.get(m['trial']))} | {_cell(m)} | {disp:.2f} |"
                )
            lines.append("")
    lines += [
        "## 出典",
        f"- 数値: `{AFTER_JSON}`（修正後、この生成）と `{BEFORE_JSON}`（修正前）。本文の数字はすべてそこから引いた",
        "- 判定値: `src/so101_demo/execution/criteria.py`。動作・補正: `src/so101_demo/execution/primitives.py`。向きの規約: `src/so101_demo/kinematics/top_down.py`。設計の説明: `docs/place-margin.md`",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=OUT_DIR)
    ap.add_argument(
        "--baseline",
        action="store_true",
        help=f"run on the code before the fix and write {BEFORE_JSON} only",
    )
    ap.add_argument("--commit", default=None, help="hash to record for this run's code")
    ap.add_argument("--no-sweep", action="store_true", help="fixed 9 trials only (quick check)")
    args = ap.parse_args(argv)
    reg = load_registry()
    today = dt.date.today().isoformat()
    # the code this run measures: the last commit that touched the pick/place path, plus
    # a flag when that path is modified in the working tree (evidence-only commits do not
    # change it, so re-running after committing the evidence reproduces the same file)
    commit = args.commit or (
        _git("log", "-1", "--format=%h", "--", *CODE_PATHS)
        + ("+dirty" if _git("status", "--porcelain", "--", *CODE_PATHS) else "")
    )
    results = run_all(reg, sweep=not args.no_sweep)
    record = {
        "date": today,
        "commit": commit,
        "config_id": CONFIG_ID,
        "margin_min_mm": MARGIN_MIN_M * 1000,
        "machine": {"node": platform.node(), "gpu": gpu_name()},
        **results,
    }
    args.out.mkdir(parents=True, exist_ok=True)
    if args.baseline:
        (args.out / BEFORE_JSON).write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"wrote {args.out / BEFORE_JSON}")
        return 0
    before_path = args.out / BEFORE_JSON
    before = json.loads(before_path.read_text(encoding="utf-8")) if before_path.is_file() else None
    if before is None:
        print(f"warning: {before_path} missing; the table has no 修正前 column", file=sys.stderr)
    (args.out / AFTER_JSON).write_text(
        json.dumps({"before": before, "after": record}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (args.out / REPORT_MD).write_text(render(before, record, reg, today), encoding="utf-8")
    ok, n, mn = _summary(record["fixed"])
    print(f"fixed: {ok}/{n} success, min margin {_mm(mn)} mm")
    print(f"wrote {args.out / REPORT_MD}")
    return 0 if all(passes(m) for m in record["fixed"]) else 1


if __name__ == "__main__":
    sys.exit(main())
