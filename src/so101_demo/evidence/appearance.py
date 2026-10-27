"""Generate docs/evidence/appearance/: the 4a fixtures at 1080p with labels (Issue #10).

    MUJOCO_GL=egl uv run python -m so101_demo.evidence.appearance --approve-fixed-fixture

Per fixture: `<fixture>-<config>-1080p.mp4` (1920x1080, 30 fps, labels), four captures
(start / lift / carry / release) as PNG, the wall-clock frame-rate statistics fixed in
Issue #10, and the segmentation visibility samples (5 Hz) of both cubes, both trays
and the moving jaw. `report.md` + `report.json` hold the numbers. The physics is the
4a scene (`sim-v1-simple`); styling is visual only (`sim.appearance`). Frame-rate
numbers are a provisional pass: Cosmos / vLLM is not running (第 3 段 同時常駐 does that).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import platform
import sys
from dataclasses import dataclass, field
from pathlib import Path

import mujoco
import numpy as np
from PIL import Image

from ..execution.runner import PlanRunner, RunRecord, approve_for_development, parse_plan
from ..plan import Registry
from ..plan.paths import FIXTURES_4A_DIR
from ..sim import appearance as A
from ..sim.model import So101Sim
from .gl_env import gl_renderer_name, prefer_gpu_on_wsl2
from .overlay import (
    FONT_PX,
    LABEL_TEXT,
    MIN_GLYPH_PX,
    LabelOverlay,
    glyph_height_px,
    margin_px,
)
from .record import AZIMUTH_DEG, DISTANCE_M, ELEVATION_DEG, LOOKAT, VideoRecorder
from .stage1_0b import _gpu_name

WIDTH, HEIGHT, FPS = 1920, 1080, 25  # frames on every 2nd control tick (CONTROL_DT_S = 0.02 s)
MIN_EFFECTIVE_FPS = 25.0
X264_PRESET = "veryfast"  # libx264 fallback only; NVENC is used when ffmpeg has it
MAX_FRAME_INTERVAL_S = 1.0
VISIBILITY_SAMPLE_DT_S = 0.2  # 5 Hz
# minimum visible pixels per sample at 1920x1080 (Issue #10 Started comment)
MIN_PX = {"target": 3000, "other": 3000, "box_a": 2000, "box_b": 2000, "jaw": 800}
# both trays and every label have to stay inside the frame (Issue #20, new tray layout):
# smallest distance to an edge, in pixels, over every sample / capture
MIN_FRAME_MARGIN_PX = 1
JAW_BODY = "moving_jaw_so101_v1"
TRAY_WALLS = ("x_pos", "x_neg", "y_pos", "y_neg")
CAPTURES = ("start", "lift", "carry", "release")
GL_NAME: dict[str, str] = {}  # GL_RENDERER, read while the first renderer is alive
CARRY_AFTER_LIFT_S = 0.7
RELEASE_AFTER_OPEN_S = 0.4


def fixtures() -> list[Path]:
    fs = sorted(FIXTURES_4A_DIR.glob("*.json"))
    if len(fs) != 4:
        raise SystemExit(f"expected 4 fixtures in {FIXTURES_4A_DIR}, found {len(fs)}")
    return fs


def geom_ids(model: mujoco.MjModel, registry: Registry) -> dict[str, np.ndarray]:
    """Segmentation ids per entity: cubes, tray walls, moving-jaw geoms."""
    ids: dict[str, np.ndarray] = {}
    for oid in registry.objects:
        ids[oid] = np.array([mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, f"{oid}_geom")])
    for did in registry.destinations:
        ids[did] = np.array(
            [
                mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, f"{did}_wall_{t}")
                for t in TRAY_WALLS
            ]
        )
    jaw = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, JAW_BODY)
    if jaw < 0:
        raise ValueError(f"body {JAW_BODY} not in the model")
    ids["jaw"] = np.flatnonzero(model.geom_bodyid == jaw)
    return ids


@dataclass
class VisibilitySampler:
    """Segmentation renderer sampled every `dt` of sim time: visible pixels per entity.
    Same camera as the video. Used by the evidence script and the gpu test."""

    sim: So101Sim
    registry: Registry
    camera: mujoco.MjvCamera
    width: int = WIDTH
    height: int = HEIGHT
    dt: float = VISIBILITY_SAMPLE_DT_S
    renderer: mujoco.Renderer | None = None  # shared segmentation renderer, or own one
    samples: list[tuple[float, dict[str, int]]] = field(default_factory=list)
    margins: dict[str, int] = field(default_factory=dict)  # entity -> smallest edge margin px

    def __post_init__(self) -> None:
        self._owns_renderer = self.renderer is None
        if self.renderer is None:
            self.renderer = mujoco.Renderer(self.sim.model, self.height, self.width)
            self.renderer.enable_segmentation_rendering()
        self._ids = geom_ids(self.sim.model, self.registry)
        self._next_t = -np.inf

    def sample(self) -> dict[str, int]:
        self.renderer.update_scene(self.sim.data, camera=self.camera)
        seg = self.renderer.render()[..., 0]
        counts: dict[str, int] = {}
        for k, ids in self._ids.items():
            mask = np.isin(seg, ids)
            counts[k] = int(mask.sum())
            if counts[k]:  # how close the visible pixels come to an edge of the frame
                rows = np.flatnonzero(mask.any(axis=1))
                cols = np.flatnonzero(mask.any(axis=0))
                m = int(
                    min(rows[0], cols[0], self.height - 1 - rows[-1], self.width - 1 - cols[-1])
                )
            else:
                m = -1  # nothing visible in this sample: not confirmed inside the frame
            self.margins[k] = min(self.margins.get(k, m), m)
        self.samples.append((float(self.sim.data.time), counts))
        return counts

    def __call__(self, sim: So101Sim) -> None:
        if sim.data.time >= self._next_t:
            self.sample()
            self._next_t = sim.data.time + self.dt - 1e-9

    def minima(self) -> dict[str, int]:
        keys = self._ids.keys()
        return {k: min((c[k] for _, c in self.samples), default=0) for k in keys}

    def close(self) -> None:
        if self._owns_renderer:
            self.renderer.close()


def visibility_verdict(
    minima: dict[str, int], target: str, other: str
) -> dict[str, tuple[int, int, bool]]:
    """(min pixels, threshold, ok) per role for one fixture."""
    roles = {
        "target": minima[target],
        "other": minima[other],
        "box_a": minima["box_a"],
        "box_b": minima["box_b"],
        "jaw": minima["jaw"],
    }
    return {k: (v, MIN_PX[k], v >= MIN_PX[k]) for k, v in roles.items()}


@dataclass
class FixtureEvidence:
    fixture_id: str
    target: str
    other: str
    destination: str
    record: RunRecord
    video: Path
    video_bytes: int
    fps: dict[str, float]
    visibility: dict[str, tuple[int, int, bool]]
    captures: dict[str, Path]
    glyph_px: dict[str, dict[str, int]]  # capture -> label -> measured height
    tray_margin_px: dict[str, int]  # tray -> smallest edge margin over the samples
    label_margin_px: dict[str, dict[str, int]]  # capture -> label -> plate edge margin
    label_margin_all_px: dict[str, int]  # label -> smallest edge margin over every frame

    @property
    def fps_ok(self) -> bool:
        return (
            self.fps["effective_fps"] >= MIN_EFFECTIVE_FPS
            and self.fps["max_interval_s"] < MAX_FRAME_INTERVAL_S
        )

    @property
    def visibility_ok(self) -> bool:
        return all(ok for _, _, ok in self.visibility.values())

    @property
    def glyph_ok(self) -> bool:
        return all(h >= MIN_GLYPH_PX for per in self.glyph_px.values() for h in per.values())

    @property
    def in_frame_ok(self) -> bool:
        """Both trays and every label fit inside the frame, on every sample and every
        recorded frame (Issue #20 acceptance). Missing measurements are not a pass: all
        four captures and at least one label box have to have been measured."""
        if not self.tray_margin_px or not self.label_margin_all_px:
            return False
        if set(self.label_margin_px) != set(CAPTURES):
            return False
        if any(not per for per in self.label_margin_px.values()):
            return False
        margins = (
            list(self.tray_margin_px.values())
            + list(self.label_margin_all_px.values())
            + [m for per in self.label_margin_px.values() for m in per.values()]
        )
        return all(m >= MIN_FRAME_MARGIN_PX for m in margins)


def run_fixture(
    runner: PlanRunner,
    path: Path,
    out: Path,
    flag: bool,
    video_renderer: mujoco.Renderer | None = None,
    seg_renderer: mujoco.Renderer | None = None,
) -> FixtureEvidence:
    """Two deterministic passes of the same fixture: video + frame-rate (nothing else on
    the GPU), then the segmentation samples. Outcome and command count must agree."""
    reg = runner.registry
    plan = approve_for_development(
        parse_plan(path.read_text(encoding="utf-8")), path.stem, approve_fixed_fixture=flag
    )
    target, dest = plan.plan.target, plan.plan.destination
    other = next(o for o in reg.objects if o != target)
    overlay = LabelOverlay(reg, WIDTH, HEIGHT)
    video = out / f"{path.stem}-{runner.config_id}-1080p.mp4"
    rec = VideoRecorder(
        runner.sim,
        video,
        width=WIDTH,
        height=HEIGHT,
        fps=FPS,
        decorate=overlay,
        brighten_headlight=False,
        preset=X264_PRESET,
        renderer=video_renderer,
        encoder="auto",
    )
    GL_NAME.setdefault("name", gl_renderer_name())
    GL_NAME.setdefault("encoder", rec.encoder)
    captured: dict[str, tuple[int, np.ndarray]] = {}
    marks: dict[str, float] = {}

    def on_tick(r: PlanRunner) -> None:
        if r._pp is None or rec.last_frame is None:
            return
        t_now = float(r.sim.data.time)
        seen = {label: t for t, label in r._pp.trace}
        if "start" not in captured:
            captured["start"] = (rec.frames - 1, rec.last_frame)
        if "pick:lifted" in seen and "lift" not in captured:
            marks["lift"] = t_now
            captured["lift"] = (rec.frames - 1, rec.last_frame)
        if (
            "lift" in marks
            and "carry" not in captured
            and t_now >= marks["lift"] + CARRY_AFTER_LIFT_S
        ):
            captured["carry"] = (rec.frames - 1, rec.last_frame)
        if "place:released" in seen and "release" not in marks:
            marks["release"] = seen["place:released"]
        if (
            "release" in marks
            and "release" not in captured
            and t_now >= marks["release"] + RELEASE_AFTER_OPEN_S
        ):
            captured["release"] = (rec.frames - 1, rec.last_frame)

    runner._observer_cb = rec
    runner.on_tick = on_tick
    try:
        record = runner.submit(plan)
    finally:
        runner.on_tick = None
        runner._observer_cb = None
        size = rec.close()
    record.video_path, record.video_bytes = video.name, size

    sampler = VisibilitySampler(runner.sim, reg, rec.camera, renderer=seg_renderer)
    runner._observer_cb = sampler
    try:
        again = runner.submit(plan)
    finally:
        runner._observer_cb = None
        sampler.close()
    if (again.outcome, again.commands_sent) != (record.outcome, record.commands_sent):
        raise RuntimeError(
            f"{path.stem}: the two passes differ ({record.outcome}, {again.outcome})"
        )

    caps: dict[str, Path] = {}
    glyphs: dict[str, dict[str, int]] = {}
    label_margins: dict[str, dict[str, int]] = {}
    all_label_margins: dict[str, int] = {}
    for boxes in overlay.boxes_by_frame.values():  # every recorded frame, not only captures
        for k, b in boxes.items():
            m = margin_px(b, WIDTH, HEIGHT)
            all_label_margins[k] = min(all_label_margins.get(k, m), m)
    (out / "captures").mkdir(parents=True, exist_ok=True)
    for name in CAPTURES:
        if name not in captured:
            continue
        idx, img = captured[name]
        p = out / "captures" / f"{path.stem}-{name}.png"
        Image.fromarray(img).save(p)
        caps[name] = p
        boxes = overlay.boxes_by_frame.get(idx, {})
        glyphs[name] = {k: glyph_height_px(img, b) for k, b in boxes.items()}
        label_margins[name] = {k: margin_px(b, WIDTH, HEIGHT) for k, b in boxes.items()}
    return FixtureEvidence(
        path.stem,
        target,
        other,
        dest,
        record,
        video,
        size,
        rec.fps_stats(),
        visibility_verdict(sampler.minima(), target, other),
        caps,
        glyphs,
        {k: sampler.margins.get(k, -1) for k in reg.destinations},
        label_margins,
        all_label_margins,
    )


def render_report(
    ev: list[FixtureEvidence], config_id: str, today: str, gl: str, env: dict[str, str]
) -> str:
    n_ok = sum(e.record.outcome == "success" for e in ev)
    fps_ok = all(e.fps_ok for e in ev)
    vis_ok = all(e.visibility_ok for e in ev)
    gly_ok = all(e.glyph_ok for e in ev)
    frame_ok = all(e.in_frame_ok for e in ev)
    machine = f"{platform.node()}、{platform.machine()}、{_gpu_name() or 'NVIDIA GPU なし'}"
    rows = []
    for e in ev:
        f = e.fps
        rows.append(
            f"| {e.fixture_id} | {LABEL_TEXT[e.target]} → {LABEL_TEXT[e.destination]} | {e.record.outcome} | {e.record.commands_sent} "
            f"| `{e.video.name}` {e.video_bytes / 1e6:.2f} MB | {f['frames']} | {f['sim_span_s']:.2f} | **{f['effective_fps']:.1f}** | {f['max_interval_s'] * 1000:.0f} | {'合' if e.fps_ok else '否'} |"
        )
    vrows = []
    for e in ev:
        cells = " | ".join(
            f"{v} / {th} {'合' if ok else '否'}" for v, th, ok in e.visibility.values()
        )
        vrows.append(f"| {e.fixture_id} | {cells} | {'合' if e.visibility_ok else '否'} |")
    tray_keys = list(ev[0].tray_margin_px) if ev else []
    tray_head = "".join(f" {LABEL_TEXT.get(k, k)} 端まで |" for k in tray_keys)
    tray_sep = "---|" * len(tray_keys)
    frows = []
    for e in ev:
        cells = "".join(f" {e.tray_margin_px.get(k, -1)} |" for k in tray_keys)
        worst_all = min(
            ((m, LABEL_TEXT.get(k, k)) for k, m in e.label_margin_all_px.items()),
            default=(-1, "測定なし"),
        )
        worst_cap = min(
            (
                (m, f"{cap} / {LABEL_TEXT.get(k, k)}")
                for cap, per in e.label_margin_px.items()
                for k, m in per.items()
            ),
            default=(-1, "測定なし"),
        )
        caps_seen = f"{len(e.label_margin_px)}/{len(CAPTURES)}"
        frows.append(
            f"| {e.fixture_id} |{cells} {worst_all[0]}（{worst_all[1]}） "
            f"| {worst_cap[0]}（{worst_cap[1]}、キャプチャ {caps_seen}） | {'合' if e.in_frame_ok else '否'} |"
        )
    grows = []
    for e in ev:
        for cap, per in e.glyph_px.items():
            cells = ", ".join(f"{LABEL_TEXT[k]} {h} px" for k, h in per.items())
            grows.append(
                f"| {e.fixture_id} | {cap} | `captures/{e.captures[cap].name}` | {cells} | {'合' if all(h >= MIN_GLYPH_PX for h in per.values()) else '否'} |"
            )
    envs = ", ".join(f"`{k}={v}`" for k, v in env.items() if v)
    return f"""# 見栄え 最小版: 4 組合せの 1080p 録画・フレームレート・可視性・ラベル（受け入れ）

- 記録日: {today}。フレームは制御 tick（`CONTROL_DT_S` = 0.02 s）2 回に 1 枚 = {FPS} fps（30 fps は tick の量子化で作れないため Issue #10 で 25 fps に修正）。生成: `MUJOCO_GL=egl uv run python -m so101_demo.evidence.appearance --approve-fixed-fixture`（再現可能。WSL2 では `so101_demo.evidence` が `GALLIUM_DRIVER=d3d12` と NVIDIA アダプタを自動選択する）
- 結論: `{config_id}` **{n_ok}/4 成功**、実効 fps **{"全 fixture で 25 以上" if fps_ok else "不足あり"}**（描画 GL: `{gl}`）、可視性 **{"全サンプル合" if vis_ok else "否あり"}**、ラベル文字高 **{"全キャプチャ 32 px 以上" if gly_ok else "不足あり"}**、両トレイとラベルが **{"全 fixture で画面内" if frame_ok else "画面外あり"}**
- 位置づけ: 母艦（{machine}）での受け入れ実行（ADR 0006）。**フレームレートは Cosmos / vLLM 停止時の仮合格**（demo-plan 見栄え行）。本判定は第 3 段 同時常駐。状態表示はこの Issue の範囲外（3b の UI ADR 後）
- 判定値と計測方法は Issue #10 の Started コメント（2026-09-19）で固定。`docs/appearance.md` に写してある
- 物理は 4a と同一（`sim-v1-simple`、`execution.criteria` 不変）。見栄えは `sim.appearance`（視覚要素のみ）で、`tests/test_appearance.py` が物理配列と軌道の一致を確認する

## 共通条件
| 項目 | 値 |
|---|---|
| 機材 | {machine}、Python {platform.python_version()}、MuJoCo {mujoco.__version__}、GL_RENDERER `{gl}` |
| GL 選択 | {envs} |
| 出力 | {WIDTH}×{HEIGHT}、H.264（encoder `{GL_NAME.get("encoder", "?")}`、品質 28。libx264 なら preset {X264_PRESET}）、{FPS} fps。動画は 実行中 区間のみ（リセット中は observer を呼ばない） |
| カメラ | 固定 1 つ。lookat {LOOKAT}、distance {DISTANCE_M} m、azimuth {AZIMUTH_DEG}°、elevation {ELEVATION_DEG}° |
| 背景 / 机 | 単色スカイボックス RGB {A.BACKGROUND_RGB}、机 平面 RGBA (0.93, 0.93, 0.90)、テクスチャなし |
| 照明 | ヘッドライト + スポットライト 1 灯（位置 {A.KEY_LIGHT_POS}、影あり、shadowsize {A.SHADOW_SIZE}） |
| ラベル | DejaVu Sans Bold {FONT_PX} px、白文字・半透明黒地・実体色の帯。`{"` / `".join(LABEL_TEXT.values())}`。物体ラベルは body 位置に追従、トレイは中心に固定 |
| fps 計測 | 各フレーム描画時の `perf_counter`。実効 fps = (フレーム数 − 1) / (最終 − 最初)、最大間隔 = 隣接フレームの壁時計差の最大。合格 ≥ {MIN_EFFECTIVE_FPS:.0f} fps かつ < {MAX_FRAME_INTERVAL_S:.1f} s |
| 可視性 | セグメンテーション描画 {WIDTH}×{HEIGHT} を sim 時間 {VISIBILITY_SAMPLE_DT_S} s ごとに採取。しきい値 px: 対象 {MIN_PX["target"]}、非対象 {MIN_PX["other"]}、box_a {MIN_PX["box_a"]}、box_b {MIN_PX["box_b"]}、可動顎 {MIN_PX["jaw"]} |
| 画面内 | トレイ: 全可視サンプルの画素の外接箱から画面端までの最小距離（見える画素が 0 のサンプルは −1 = 確認できず）。ラベル: 録画した全フレームと 4 枚のキャプチャで、地色の箱（文字 + 余白 + 色帯）から画面端までの最小距離。合格 ≥ {MIN_FRAME_MARGIN_PX} px（0 = 端に接する、負 = 切れている）。キャプチャが 4 枚そろわない、またはラベルを 1 つも測れていない fixture は 否 |

## 4 組合せ: 結果とフレームレート
| fixture | 対象 → 置き先 | 結果 | 指令数 | 動画 | フレーム数 | 実行 sim 時間 s | 実効 fps | 最大間隔 ms | fps 判定 |
|---|---|---|---|---|---|---|---|---|---|
{chr(10).join(rows)}

## 可視性（各 fixture、全サンプルの最小画素数 / しきい値）
| fixture | 対象 | 非対象 | box_a | box_b | 可動顎 | 判定 |
|---|---|---|---|---|---|---|
{chr(10).join(vrows)}

## 画面内（トレイとラベルが {WIDTH}×{HEIGHT} に収まるか、単位 px）
| fixture |{tray_head} ラベル最小 全フレーム（ラベル） | ラベル最小 キャプチャ（場面 / ラベル） | 判定 |
|---|{tray_sep}---|---|---|
{chr(10).join(frows)}

## ラベル文字高（キャプチャ上の白画素の行範囲、合格 ≥ {MIN_GLYPH_PX} px）
| fixture | 場面 | ファイル | 各ラベル | 判定 |
|---|---|---|---|---|
{chr(10).join(grows)}

## 注記
- 表示した対象と動いた対象の一致: 各 fixture の対象ラベルは `contracts/registry.json` の id から引いた文字列で、動画中はその body の位置に追従する。判定器の success（対象が置き先に着座）と上表の「対象 → 置き先」が同じ id を指す
- キャプチャは encoder に送った生フレーム（PNG）。動画（H.264）より劣化がないので、文字高の計測は表示品質の上限側の値になる
- 画面内の判定は ADR 0008 の新配置（トレイ (0.22, ±0.09)）で追加した（Issue #20 母艦分）。トレイの余裕はセグメンテーション（見えている画素）で測るので、腕に隠れている間は隠れた分を除いた外接箱になる。ラベルは搬送中に物体を追って動くため、キャプチャ 4 枚だけでなく録画した全フレーム（各 fixture 245 枚前後）で測る
- 4a の 640×480 動画（`docs/evidence/4a/`）はそのまま残す。この記録は G1 の受け入れ試行ではない
"""


def main(argv: list[str] | None = None) -> int:
    env = prefer_gpu_on_wsl2()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default="sim-v1-simple")
    ap.add_argument("--out", type=Path, default=Path("docs/evidence/appearance"))
    ap.add_argument(
        "--approve-fixed-fixture", action="store_true", help="the only development approval path"
    )
    args = ap.parse_args(argv)
    if not args.approve_fixed_fixture:
        print(
            "refusing to run: the 4a fixtures need --approve-fixed-fixture (docs/execution-runner.md)",
            file=sys.stderr,
        )
        return 2
    args.out.mkdir(parents=True, exist_ok=True)
    runner = PlanRunner(args.config, styled=True)
    video_r = mujoco.Renderer(runner.sim.model, HEIGHT, WIDTH)
    seg_r = mujoco.Renderer(runner.sim.model, HEIGHT, WIDTH)
    seg_r.enable_segmentation_rendering()
    try:
        ev = [run_fixture(runner, f, args.out, True, video_r, seg_r) for f in fixtures()]
    finally:
        video_r.close()
        seg_r.close()
    gl = GL_NAME.get("name", "unknown")
    today = dt.date.today().isoformat()
    (args.out / "report.md").write_text(
        render_report(ev, args.config, today, gl, env), encoding="utf-8"
    )
    log = {
        "date": today,
        "config_id": args.config,
        "gl_renderer": gl,
        "gl_env": env,
        "machine": {"node": platform.node(), "gpu": _gpu_name()},
        "thresholds": {
            "min_effective_fps": MIN_EFFECTIVE_FPS,
            "max_frame_interval_s": MAX_FRAME_INTERVAL_S,
            "min_px": MIN_PX,
            "min_glyph_px": MIN_GLYPH_PX,
            "min_frame_margin_px": MIN_FRAME_MARGIN_PX,
            "font_px": FONT_PX,
        },
        "fixtures": [
            {
                "fixture_id": e.fixture_id,
                "target": e.target,
                "destination": e.destination,
                "outcome": e.record.outcome,
                "commands_sent": e.record.commands_sent,
                "video": e.video.name,
                "video_bytes": e.video_bytes,
                "fps": e.fps,
                "fps_ok": e.fps_ok,
                "visibility": {
                    k: {"min_px": v, "threshold": th, "ok": ok}
                    for k, (v, th, ok) in e.visibility.items()
                },
                "visibility_ok": e.visibility_ok,
                "captures": {k: p.name for k, p in e.captures.items()},
                "glyph_px": e.glyph_px,
                "glyph_ok": e.glyph_ok,
                "tray_margin_px": e.tray_margin_px,
                "label_margin_px": e.label_margin_px,
                "label_margin_all_px": e.label_margin_all_px,
                "in_frame_ok": e.in_frame_ok,
                "run": e.record.as_dict(),
            }
            for e in ev
        ],
    }
    (args.out / "report.json").write_text(
        json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for e in ev:
        print(
            f"{e.fixture_id}: {e.record.outcome}, {e.fps['effective_fps']:.1f} fps (max gap {e.fps['max_interval_s'] * 1000:.0f} ms) fps_ok={e.fps_ok} vis_ok={e.visibility_ok} glyph_ok={e.glyph_ok} in_frame_ok={e.in_frame_ok}"
        )
    print(f"GL: {gl}; wrote {args.out / 'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
