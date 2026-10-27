"""How much room the tray layout leaves for the G1 start positions (Issue #19, ADR 0008).

    uv run python -m so101_demo.evidence.g1_layout

Writes docs/evidence/g1/layout.md + layout.json: for the registered tray layout and the
alternatives weighed in ADR 0008, how many 10 mm grid points of the work area a cube can
stand on, how many two-cube start patterns those points allow, and which of the ten
manifest trials the layout permits. Geometry only -- `plan.manifest.start_violations` is
the single implementation of the rules and no simulation runs here, so the numbers are
reproducible on any machine in under a second.

This replaces the sweep module `g1_candidates` of 2026-09-20 (deleted 2026-09-21): ADR
0008 moved the choice of the start positions from "the agent selects by a rule over
headless runs, the other cube stays home" to "the user picks ten two-cube patterns from
the rendered layout, the agent checks the rules and runs them" (docs/g1-manifest.md).
The old layout's sweep stays in docs/evidence/g1/tray19/candidates.md.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import platform
import sys
from pathlib import Path

import numpy as np

from ..kinematics.top_down import WORKSPACE_MAX, WORKSPACE_MIN
from ..plan import Registry, load_g1_manifest, load_registry
from ..plan.manifest import MIN_CENTRE_DISTANCE_M, G1Manifest, start_violations
from ..plan.paths import G1_MANIFEST_PATH, REPO_ROOT

GRID_M = 0.010
OUT_DIR = Path("docs/evidence/g1")
# The tray centres (x, y) weighed in ADR 0008 (metres): the layout of Issue #4 used
# until 2026-09-20, and the two far-corner candidates. box_a sits at (x, +y) and box_b
# at (x, -y). The registered one is marked in the output by comparing with
# contracts/registry.json.
LAYOUTS_M: dict[str, tuple[float, float]] = {
    "tray19": (0.19, 0.085),
    "tray22": (0.22, 0.09),
    "tray23": (0.23, 0.09),
}


def _rel(path: Path) -> str:
    """Repo-relative path, so the written report does not depend on the checkout."""
    p = Path(path)
    return (
        str(p.relative_to(REPO_ROOT)) if p.is_absolute() and p.is_relative_to(REPO_ROOT) else str(p)
    )


def grid_points() -> list[tuple[float, float]]:
    xs = np.round(np.arange(WORKSPACE_MIN[0], WORKSPACE_MAX[0] + GRID_M / 2, GRID_M), 3)
    ys = np.round(np.arange(WORKSPACE_MIN[1], WORKSPACE_MAX[1] + GRID_M / 2, GRID_M), 3)
    return [(float(x), float(y)) for x in xs for y in ys]


def with_trays(reg: Registry, x_m: float, y_m: float) -> Registry:
    """The registry with box_a at (x, +y) and box_b at (x, -y); everything else as is.

    The mirrored pair is the shape ADR 0008 compares; a third destination would have no
    place in it, so refuse rather than silently stack it on box_b."""
    if set(reg.destinations) != {"box_a", "box_b"}:
        raise ValueError(f"expected destinations box_a/box_b, got {sorted(reg.destinations)}")
    dests = {
        did: dataclasses.replace(d, center_xy_m=(x_m, y_m if did == "box_a" else -y_m))
        for did, d in reg.destinations.items()
    }
    return dataclasses.replace(reg, destinations=dests)


def standable(reg: Registry, object_id: str) -> list[tuple[float, float]]:
    """Grid points where the cube can start on its own: inside the work area, off the
    trays, grasp TCP reachable. The distance rule needs a second object and is applied
    by `pair_count`."""
    return [xy for xy in grid_points() if not start_violations(reg, object_id, xy, {})]


def pair_count(reg: Registry) -> int:
    """Two-cube start patterns the layout allows: unordered {red position, blue position}
    with both cubes standable and at least MIN_CENTRE_DISTANCE_M apart."""
    red = set(standable(reg, "red_cube"))
    blue = set(standable(reg, "blue_cube"))
    pts = grid_points()
    n = 0
    for i, p in enumerate(pts):
        for q in pts[i + 1 :]:
            if np.linalg.norm(np.asarray(p) - np.asarray(q)) < MIN_CENTRE_DISTANCE_M - 1e-9:
                continue
            if (p in red and q in blue) or (q in red and p in blue):
                n += 1
    return n


def manifest_trials_allowed(reg: Registry, m: G1Manifest) -> tuple[int, list[str]]:
    """How many manifest trials the layout permits, and which ones it refuses."""
    refused = [
        t.trial
        for t in m.trials
        if any(start_violations(reg, oid, xy, t.start_xy_m) for oid, xy in t.start_xy_m.items())
    ]
    return len(m.trials) - len(refused), refused


def measure(reg: Registry, m: G1Manifest) -> list[dict]:
    rows = []
    for name, (x, y) in LAYOUTS_M.items():
        r = with_trays(reg, x, y)
        red, blue = standable(r, "red_cube"), standable(r, "blue_cube")
        ok, refused = manifest_trials_allowed(r, m)
        rows.append(
            {
                "layout": name,
                "tray_center_xy_m": [x, y],
                "registered": tuple(reg.destinations["box_a"].center_xy_m) == (x, y),
                "standable_red": len(red),
                "standable_blue": len(blue),
                "pairs": pair_count(r),
                "manifest_trials_allowed": ok,
                "manifest_trials_refused": refused,
            }
        )
    # The report marks the registered layout; a registry moved off this list would drop
    # the marker without any other sign, so fail instead of writing a report without it.
    if sum(r["registered"] for r in rows) != 1:
        raise ValueError(
            f"registered tray centre {reg.destinations['box_a'].center_xy_m} "
            f"matches {sum(r['registered'] for r in rows)} of the compared layouts; "
            "add it to LAYOUTS_M"
        )
    return rows


def render(rows: list[dict], reg: Registry, m: G1Manifest, today: str) -> str:
    grid = grid_points()
    full = ", ".join(r["layout"] for r in rows if r["manifest_trials_allowed"] == len(m.trials))
    short = ", ".join(
        f"{r['layout']}（{r['manifest_trials_allowed']}/{len(m.trials)}）"
        for r in rows
        if r["manifest_trials_allowed"] < len(m.trials)
    )
    head = (
        f"# G1 開始位置の余地: トレイ配置ごとの格子点数（{today}）\n\n"
        f"- 生成: `uv run python -m so101_demo.evidence.g1_layout`（幾何のみ、sim 実行なし、再現可能）。機材: {platform.node()}\n"
        f"- 格子: 作業領域 x {WORKSPACE_MIN[0]:.2f}〜{WORKSPACE_MAX[0]:.2f}、y {WORKSPACE_MIN[1]:.2f}〜{WORKSPACE_MAX[1]:.2f} を {GRID_M * 1000:.0f} mm 刻み（{len(grid)} 点）\n"
        f"- 規則は manifest と同一実装（`plan.manifest.start_violations`）: 作業領域内、立方体の足元がトレイ外形（内寸 {reg.destinations['box_a'].inner_m * 1000:.0f} + 壁 {reg.destinations['box_a'].wall_thickness_m * 1000:.0f} mm）と重ならない、把持 TCP が作業領域内。「2 物体の配置」はこれに中心間距離 ≥ {MIN_CENTRE_DISTANCE_M * 1000:.0f} mm を加えた無順序の組\n"
        f"- 用途: ADR 0008 でトレイを (0.22, ±0.09) へ移した根拠の数値。合否の判定はしない\n\n"
        "| 配置 | トレイ中心 xy m | 立方体が立てる格子点（red / blue） | 2 物体の配置 | manifest 10 試行のうち規則を満たす | 登録値 |\n"
        "|---|---|---|---|---|---|\n"
    )
    body = "".join(
        f"| {r['layout']} | ({r['tray_center_xy_m'][0]:.3f}, ±{r['tray_center_xy_m'][1]:.3f}) "
        f"| {r['standable_red']} / {r['standable_blue']} | {r['pairs']} "
        f"| {r['manifest_trials_allowed']}/{len(m.trials)}"
        f"{'' if not r['manifest_trials_refused'] else '（不可: ' + ', '.join(r['manifest_trials_refused']) + '）'} "
        f"| {'**現行**' if r['registered'] else '-'} |\n"
        for r in rows
    )
    return (
        head
        + body
        + (
            "\n## 読み方\n"
            "- 立方体が立てる格子点は red / blue で同じになる（登録上の寸法と yaw が同じで、規則は色に依らない）。"
            "違いが出るとしたら登録値を変えたときだけである\n"
            "- x 方向の下限は把持 TCP（中心から基部側へ 23.5 mm）で決まり、トレイ位置に依らず x ≥ 0.16 m の列しか残らない。"
            "トレイを奥の両隅へ寄せると空くのは y 方向である\n"
            f"- manifest（`{_rel(m.path)}`）の {len(m.trials)} 試行は ADR 0008 の表そのもので、"
            f"全 {len(m.trials)} 試行が規則を満たすのは {full}。{short}では作れない\n"
            "- 「2 物体の配置」は無順序の組なので、赤青のどちらをどちらに割り当てるかを区別した"
            "開始パターン数はこの 2 倍である（立てる格子点が red / blue で同じため）\n"
            "\n## 出典\n"
            "- `docs/adr/0008-move-trays-to-far-corners-and-fix-both-start-positions.md`（決定と比較表）\n"
            "- `contracts/registry.json`（登録トレイ位置・寸法）、"
            "`src/so101_demo/kinematics/top_down.py`（作業領域）、"
            "`src/so101_demo/plan/manifest.py`（規則の実装）\n"
            "- 旧配置での候補 sweep（物理実行つき、2026-09-20）: `docs/evidence/g1/tray19/candidates.md`\n"
        )
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=OUT_DIR)
    ap.add_argument("--manifest", type=Path, default=G1_MANIFEST_PATH)
    args = ap.parse_args(argv)
    reg = load_registry()
    m = load_g1_manifest(args.manifest)
    rows = measure(reg, m)
    today = dt.date.today().isoformat()
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "layout.md").write_text(render(rows, reg, m, today), encoding="utf-8")
    (args.out / "layout.json").write_text(
        json.dumps(
            {
                "date": today,
                "grid_m": GRID_M,
                "min_centre_distance_m": MIN_CENTRE_DISTANCE_M,
                "work_area_m": {"min": WORKSPACE_MIN.tolist(), "max": WORKSPACE_MAX.tolist()},
                "manifest": _rel(args.manifest),
                "layouts": rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    for r in rows:
        print(
            f"{r['layout']}: standable {r['standable_red']}, pairs {r['pairs']}, "
            f"manifest {r['manifest_trials_allowed']}/{len(m.trials)}"
        )
    print(f"wrote {args.out / 'layout.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
