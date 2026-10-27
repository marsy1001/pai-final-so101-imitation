"""The four paths with real understanding results, and screen captures (Issue #27, GPU part).

    uv run python -m so101_demo.ui.evidence_3b_gpu

The understanding results are the rows of the Uc evaluation, revision 2
(`docs/evidence/uc/eval-r2.json`, fixed configuration `config-r2.json`, #25): each row's
Cosmos sentence and validated plan are handed to the screen as a reading's result, so
the screen shows exactly what the understanding layer returned for that acceptance
video. Nothing calls Cosmos or Astra here (the chain itself is joined in 3c).

The app runs as `python -m so101_demo.ui --render` does (styled sim rendered on the
runner thread, JPEG to /big) on a real uvicorn server. The operations go over HTTP;
the Windows browser (Edge, headless, GPU off as in production) takes 1920x1080
captures of both screens in each phase and reports the font size of every visible text.

Writes docs/evidence/3b/operations-gpu.md (conclusion first), operations-gpu.json and
captures/*.png. Exits 1 when a path does not end as expected, a label is under 32 px,
or a capture fails. A run without an NVIDIA GPU is labelled 参考 (ADR 0006).
"""

from __future__ import annotations

import html
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from ..plan import Plan, validate_plan
from ..plan.paths import REPO_ROOT
from .evidence_3b import RUN_WAIT_S, ScriptedSource, _git_head, _pair

OUT_DIR = REPO_ROOT / "docs" / "evidence" / "3b"
CAPTURES = OUT_DIR / "captures"
UC_EVAL = REPO_ROOT / "docs" / "evidence" / "uc" / "eval-r2.json"
PORT = 8766
# Every phase is captured on both screens; a missing one fails the evidence.
STATES = ("idle", "reading", "result", "running", "failed", "manual", "prerecorded")
SCREENS = ("approval", "big")
TITLES = {"/": "承認画面", "/big": "シミュレーション"}  # <title> of approval.html / big.html
EDGE = Path("/mnt/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")
MIN_LABEL_PX = 32.0  # demo-plan 見栄えの最低ライン: main labels at 1080p, about 32 px and up

# Every element whose own text is visible; the page is loaded in a 1920x1080 iframe.
MEASURE_PAGE = """<!doctype html><meta charset="utf-8">
<body style="margin:0"><iframe id="f" src="{page}" style="width:1920px;height:1080px;border:0">
</iframe><script>
const f = document.getElementById("f");
f.onload = () => setTimeout(() => {{
  const d = f.contentDocument, out = [];
  for (const el of d.body.querySelectorAll("*")) {{
    const own = [...el.childNodes].filter((n) => n.nodeType === 3)
      .map((n) => n.textContent.trim()).join("");
    if (!own || el.closest("[hidden]") || !el.getClientRects().length) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility === "hidden" || cs.display === "none") continue;
    out.push({{id: el.id || el.className || el.tagName.toLowerCase(), text: own.slice(0, 40),
      font_px: parseFloat(cs.fontSize)}});
  }}
  const pre = document.createElement("pre");
  pre.id = "out";
  pre.textContent = JSON.stringify(out);
  document.body.appendChild(pre);
}}, 1500);
</script>"""


def uc_results() -> dict[str, dict[str, Any]]:
    """Per acceptance video: the sentence and plan the fixed configuration returned, and gold."""
    from ..evidence.uc_eval import load_manifest

    _, videos = load_manifest()
    gold = {v.id: v for v in videos}
    out = {}
    for row in json.loads(UC_EVAL.read_text(encoding="utf-8"))["rows"]:
        v = gold[row["video"]]
        out[row["video"]] = {
            "sentence": row["cosmos"]["sentence"] or "",
            "plan": validate_plan(row["plan"]),
            "scope": v.scope,
            "gold": {k: v.gold.get(k) for k in ("status", "target", "destination")},
        }
    return out


class PauseOnce:
    """Holds the runner thread mid-motion once, so the 実行中 capture shows a moving arm."""

    def __init__(self) -> None:
        self.at: int | None = None
        self.reached = threading.Event()
        self.go = threading.Event()

    def arm(self, at_tick: int) -> None:
        self.at, self.n = at_tick, 0
        self.reached.clear()
        self.go.clear()

    def wrap(self, make):
        def observer(sim):
            inner = make(sim)

            def on_tick(s) -> None:
                inner(s)
                if self.at is not None:
                    self.n += 1
                    if self.n == self.at:
                        self.at = None
                        self.reached.set()
                        self.go.wait(60)

            return on_tick

        return observer


class Browser:
    """Windows Edge, headless with the GPU off (production: hardware acceleration off)."""

    def __init__(self, base: str) -> None:
        self.base = base
        self.tmp = Path("/tmp/so101-3b-captures")
        self.tmp.mkdir(exist_ok=True)

    def _edge(self, *args: str) -> str:
        cmd = [str(EDGE), "--headless=new", "--disable-gpu", "--hide-scrollbars"]
        cmd += ["--window-size=1920,1080", "--virtual-time-budget=4000", *args]
        return subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout

    def phase_shown(self, page: str) -> str | None:
        """The phase the page draws, read from its DOM after the scripts ran; None when the
        page did not load (an Edge error page has neither the title nor the phase)."""
        try:
            dom = self._edge("--dump-dom", self.base + page)
        except subprocess.TimeoutExpired:
            return None
        if f"<title>{TITLES[page]}</title>" not in dom:
            return None
        m = re.search(r'id="phase"[^>]*>([^<]*)<', dom)
        return html.unescape(m.group(1)) if m else None

    def wait_ready(self, timeout_s: float = 60.0) -> bool:
        """Windows reaches a new WSL port only after a moment; the first capture must not
        be Edge's connection error."""
        end = time.monotonic() + timeout_s
        while time.monotonic() < end:
            if self.phase_shown("/") is not None:
                return True
            time.sleep(1)
        return False

    def capture(self, page: str, name: str, phase: str) -> Path | None:
        """The capture in docs/evidence/3b/captures/, or None when Edge wrote nothing or the
        page did not show `phase` just before and just after the screenshot."""
        if self.phase_shown(page) != phase:
            return None
        tmp = self.tmp / f"{name}.png"
        tmp.unlink(missing_ok=True)
        win = subprocess.run(["wslpath", "-w", str(tmp)], capture_output=True, text=True)
        try:
            self._edge(f"--screenshot={win.stdout.strip()}", self.base + page)
        except subprocess.TimeoutExpired:
            return None
        if win.returncode != 0 or not tmp.is_file() or self.phase_shown(page) != phase:
            return None
        CAPTURES.mkdir(parents=True, exist_ok=True)
        out = CAPTURES / f"{name}.png"
        shutil.copyfile(tmp, out)
        return out

    def fonts(self, page: str) -> list[dict]:
        try:
            dom = self._edge("--dump-dom", f"{self.base}/measure?page={page}")
        except subprocess.TimeoutExpired:
            return []
        m = re.search(r'<pre id="out">(.*?)</pre>', dom, re.S)
        return json.loads(html.unescape(m.group(1))) if m else []


def _png_size(path: Path) -> tuple[int, int]:
    head = path.read_bytes()[:24]
    return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")


def _gpu() -> str | None:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() or None if out.returncode == 0 else None


def run_all() -> dict:
    os.environ.setdefault("MUJOCO_GL", "egl")
    from ..evidence.gl_env import prefer_gpu_on_wsl2

    prefer_gpu_on_wsl2()

    import httpx
    import uvicorn
    from fastapi.responses import HTMLResponse

    from ..execution.runner import PlanRunner
    from .frames import FrameStore, renderer_observer
    from .runner_thread import RunnerThread
    from .screen import ApprovalScreen
    from .server import create_app

    frames = FrameStore()
    pause = PauseOnce()
    rt = RunnerThread(
        lambda: PlanRunner(observer=pause.wrap(renderer_observer(frames)), styled=True)
    )
    screen = ApprovalScreen(executor=rt.submit)
    source = ScriptedSource()
    app = create_app(screen, source, frames)

    @app.get("/measure")
    def measure(page: str) -> HTMLResponse:  # evidence only; not part of the app
        return HTMLResponse(MEASURE_PAGE.format(page=html.escape(page)))

    server = uvicorn.Server(uvicorn.Config(app, port=PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    base = f"http://localhost:{PORT}"
    client = httpx.Client(base_url=base, timeout=30)
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)
    browser = Browser(base)
    if not browser.wait_ready():
        raise RuntimeError(f"Edge cannot open {base}/")
    uc = uc_results()
    runner = rt.runner

    def view() -> dict:
        return client.get("/api/view").json()

    def post(op: str, body: dict | None = None) -> int:
        body = {"session": view()["session"], **(body or {})}
        return client.post(f"/api/{op}", json=body).status_code

    captures: list[dict] = []

    def shoot(state: str) -> None:
        phase = view()["phase"]
        for page, screen_name in (("/", "approval"), ("/big", "big")):
            path = browser.capture(page, f"{state}-{screen_name}", phase)
            fonts = browser.fonts(page) if path else []
            captures.append(
                {
                    "state": state,
                    "phase": phase,
                    "screen": screen_name,
                    "file": str(path.relative_to(REPO_ROOT)) if path else None,
                    "size": list(_png_size(path)) if path else None,
                    "texts": fonts,
                    "min_font_px": min((t["font_px"] for t in fonts), default=None),
                }
            )

    rows: list[dict] = []

    def record(path: str, video: str, expect: str, steps, check) -> None:
        n0 = len(runner.records)
        codes = steps()
        if not screen.wait_idle(RUN_WAIT_S):
            raise RuntimeError("the run did not finish")
        new = runner.records[n0:]
        entry = screen.log[-1].as_dict()
        r = uc[video]
        rows.append(
            {
                "path": path,
                "video": video,
                "scope": r["scope"],
                "gold": r["gold"],
                "sentence": r["sentence"],
                "note": r["plan"].note,
                "expect": expect,
                "http": codes,
                "runs": len(new),
                "outcome": new[-1].outcome if new else None,
                "commands_sent": new[-1].commands_sent if new else 0,
                "approval_source": new[-1].approval_source if new else None,
                "log": entry,
                "phase_after": view()["phase"],
                "ok": check(new, entry),
            }
        )

    def push(video: str, *, hold: bool = False) -> Plan:
        source.push(uc[video]["plan"], uc[video]["sentence"], hold=hold)
        return uc[video]["plan"]

    def ran(new, e, target, destination, rescued) -> bool:
        return (
            len(new) == 1
            and new[0].outcome == "success"
            and e["rescued"] is rescued
            and e["final"]["target"] == target
            and e["final"]["destination"] == destination
        )

    shoot("idle")

    # 承認: uc-02 came back right (red_cube -> box_a); captures reading, result, running
    def approve_steps():
        push("uc-02", hold=True)
        codes = [("read", post("read"))]
        shoot("reading")
        source.release()
        shoot("result")
        pause.arm(40)
        try:
            codes.append(("approve", post("approve")))
            if pause.reached.wait(RUN_WAIT_S):  # else no 実行中 capture: the evidence fails
                shoot("running")
        finally:
            pause.go.set()
        return codes

    record(
        "承認",
        "uc-02",
        "抽出結果（正解どおり）をそのまま実行",
        approve_steps,
        lambda new, e: ran(new, e, "red_cube", "box_a", False),
    )
    # 訂正: uc-04 came back with left and right swapped; the visitor corrects the tray
    push("uc-04")
    record(
        "訂正",
        "uc-04",
        "左右を取り違えた結果（box_a）を、正解の box_b に訂正して実行",
        lambda: [
            ("read", post("read")),
            ("correct", post("correct", {"target": "red_cube", "destination": "box_b"})),
        ],
        lambda new, e: (
            ran(new, e, "red_cube", "box_b", True) and e["shown"]["destination"] == "box_a"
        ),
    )

    # 訂正 from needs_review: uc-01 added a second move that did not happen
    def needs_review_steps():
        push("uc-01")
        codes = [("read", post("read"))]
        shoot("failed")
        codes.append(("correct", post("correct", {"target": "red_cube", "destination": "box_a"})))
        return codes

    record(
        "訂正",
        "uc-01",
        "確認が要る結果（needs_review）から、正解の red_cube → box_a を選んで実行",
        needs_review_steps,
        lambda new, e: ran(new, e, "red_cube", "box_a", True),
    )
    # 拒否: uc-13 (pen, unsupported) and uc-15 (the false accept of revision 2)
    push("uc-13")
    record(
        "拒否",
        "uc-13",
        "対応外と返った結果を対応外として拒否し、何も動かさない",
        lambda: [("read", post("read")), ("reject", post("reject"))],
        lambda new, e: len(new) == 0 and e["path"] == "拒否" and e["final"] is None,
    )
    push("uc-15")
    record(
        "拒否",
        "uc-15",
        "対応外の動画に実行可能な案（誤受理）が返ったのを、人が対応外として拒否する",
        lambda: [("read", post("read")), ("reject", post("reject"))],
        lambda new, e: len(new) == 0 and e["path"] == "拒否" and e["shown"]["status"] == "ok",
    )

    # 手動: uc-07 failed in Cosmos (output cut at the token limit)
    def manual_steps():
        push("uc-07")
        codes = [("read", post("read")), ("manual", post("manual"))]
        shoot("manual")
        codes.append(("choose", post("choose", {"target": "blue_cube", "destination": "box_a"})))
        return codes

    record(
        "手動",
        "uc-07",
        "読み取り失敗（error）から候補を選び、正解の blue_cube → box_a を実行",
        manual_steps,
        lambda new, e: ran(new, e, "blue_cube", "box_a", True),
    )
    post("prerecorded")
    shoot("prerecorded")
    post("idle")
    server.should_exit = True

    small = [
        {**t, "state": c["state"], "screen": c["screen"]}
        for c in captures
        for t in c["texts"]
        if t["font_px"] < MIN_LABEL_PX
    ]
    taken = {(c["state"], c["screen"]) for c in captures}
    missing = [f"{st}-{sc}" for st in STATES for sc in SCREENS if (st, sc) not in taken]
    captures_ok = not missing and all(c["size"] == [1920, 1080] and c["texts"] for c in captures)
    gpu = _gpu()
    return {
        "commit": _git_head(),
        "date": time.strftime("%Y-%m-%d"),
        "machine": f"{platform.node()} ({platform.machine()})",
        "gpu": gpu,
        "label": "受け入れ（母艦）" if gpu else "参考（GPU なし）",
        "source": str(UC_EVAL.relative_to(REPO_ROOT)),
        "frames_rendered": frames.count,
        "paths": rows,
        "captures": captures,
        "small_texts": small,
        "missing_captures": missing,
        "log": [e.as_dict() for e in screen.log],
        "passed": all(r["ok"] for r in rows) and captures_ok and not small,
    }


def report(result: dict) -> str:
    rows, caps = result["paths"], result["captures"]
    fonts = sorted({t["font_px"] for c in caps for t in c["texts"]})
    lines = [
        f"# 3b 承認画面: 実際の抽出結果での 4 経路と画面キャプチャ（{result['label']}）",
        "",
        "## 結論",
        "",
        f"- 実際の抽出結果（Uc 改訂 2 の固定構成、受け入れ動画 {len(rows)} 本分）で、"
        f"4 経路（承認・訂正・対応外拒否・手動）の {len(rows)} 操作のうち "
        f"{sum(r['ok'] for r in rows)} 操作が想定どおりに終わった。"
        "承認・訂正・手動は描画つきの sim で実行して成功し、拒否は runner に何も渡らなかった",
        "- uc-15 の誤受理（対応外の動画に実行可能な案が返った）は、承認画面で人が"
        "「対応外にする」を押せば動かない。自動理解の成否は #25 / #26 の範囲で、ここでは"
        "画面の経路だけを確かめた",
        f"- 画面キャプチャ: 承認画面と大画面を 1920×1080 で {len(caps)} 枚"
        "（`docs/evidence/3b/captures/`）。表示中の文字の大きさは "
        f"{', '.join(f'{f:g}' for f in fonts)} px"
        + (
            f" で、すべて {MIN_LABEL_PX:g} px 以上"
            if not result["small_texts"]
            else f"。**{MIN_LABEL_PX:g} px 未満の文字がある**（下表）"
        ),
        "- 訂正と手動はログで `rescued: true`。自動理解の成功には数えない",
        f"- 判定: {'合格' if result['passed'] else '不合格'}（{result['label']}）",
        "",
        "## 条件",
        "",
        f"- 生成 commit: `{result['commit']}`（{result['date']}）、端末: {result['machine']}、"
        f"GPU: {result['gpu'] or 'なし'}",
        "- コマンド: `uv run python -m so101_demo.ui.evidence_3b_gpu`",
        f"- 抽出結果: `{result['source']}` の各行の Cosmos 一文と検証済みプラン（#25、固定構成 "
        "`config-r2.json`）を、読み取りの結果として画面に渡した。Cosmos・Astra はここでは呼ばない"
        "（つなぐのは 3c）",
        "- 経路: 実際の uvicorn サーバー → `ApprovalScreen` → 専用スレッドの `PlanRunner`"
        f"（styled、1280×720 JPEG を大画面へ。描画した枚数 {result['frames_rendered']}）。"
        "操作は HTTP で送った",
        "- キャプチャ: Windows 側の Edge を headless・GPU 無効（本番でハードウェア"
        "アクセラレーションを切るのに合わせた）・1920×1080 で開いた。文字の大きさは、"
        "同じ画面を 1920×1080 の iframe に読み込み、見えている文字を持つ要素すべての "
        "`font-size` を読んだ（`/measure`、この証拠スクリプトだけが足す）。"
        "各キャプチャの直前と直後に同じページの DOM を読み、タイトルと状態表示がサーバーの"
        "状態と一致したときだけ採った（Edge のエラーページや別の状態を採らない）",
        "- 実行中のキャプチャは、sim を動作の途中（40 tick 目）で一時停止して撮った",
        "",
        "## 4 経路（実際の抽出結果）",
        "",
        "| 経路 | 動画 | 正解 | 表示した案 | 実行したプラン | runner の結果 | 救済 | 判定 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        e = r["log"]
        lines.append(
            f"| {r['path']} | {r['video']} | {_pair(r['gold'])} | {_pair(e['shown'])} | "
            f"{_pair(e['final'])} | {r['outcome'] or '実行なし'} | "
            f"{'はい' if e['rescued'] else 'いいえ'} | {'OK' if r['ok'] else 'NG'} |"
        )
    lines += ["", "想定:", ""]
    lines += [f"- {r['path']}（{r['video']}）: {r['expect']}" for r in rows]
    lines += [
        "",
        "画面に出た「読み取った内容」（プランの `note`、日本語）と、その元になった Cosmos の一文"
        "（画面には出さない）:",
        "",
    ]
    lines += [f"- {r['video']}: {r['note']}（Cosmos: {r['sentence'] or 'なし'}）" for r in rows]
    lines += [
        "",
        "## 画面キャプチャと文字の大きさ",
        "",
        "| 状態 | 画面 | ファイル | 大きさ | 最小の文字 (px) |",
        "| --- | --- | --- | --- | --- |",
    ]
    for c in caps:
        name = f"`{Path(c['file']).name}`" if c["file"] else "**撮れなかった**"
        lines.append(
            f"| {c['phase']} | {'承認画面' if c['screen'] == 'approval' else '大画面'} | {name} | "
            f"{'×'.join(map(str, c['size'])) if c['size'] else '-'} | "
            f"{c['min_font_px'] if c['min_font_px'] is not None else '-'} |"
        )
    if result["missing_captures"]:
        lines += ["", f"**撮れなかったキャプチャ**: {', '.join(result['missing_captures'])}"]
    if result["small_texts"]:
        lines += ["", f"{MIN_LABEL_PX:g} px 未満の文字:", ""]
        lines += [
            f"- {t['state']} / {t['screen']}: `{t['id']}`「{t['text']}」{t['font_px']} px"
            for t in result["small_texts"]
        ]
    lines += [
        "",
        "要素ごとの値は `operations-gpu.json` の `captures[].texts`。",
        "",
        "## 既知の問題",
        "",
        "- なし（Cosmos の英語の一文は 2026-09-29 のユーザー決定で画面から外し、プランの日本語 "
        "`note` を出す）",
        "",
        "## 出典",
        "",
        "- `docs/demo-plan.md` 第 2 段の表「3b 承認画面」、「見栄えの最低ライン」（1080p で主要"
        "ラベル概ね 32 px 以上、状態表示）、共通規約（救済分は成功率に数えない）",
        "- `docs/adr/0011-browser-ui-for-approval-and-big-screen.md`",
        "- 抽出結果: `docs/evidence/uc/eval-r2.md`（#25）",
        "- 実装: `src/so101_demo/ui/`",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    if not EDGE.exists():
        print(f"Edge not found at {EDGE}", file=sys.stderr)
        return 1
    result = run_all()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "operations-gpu.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (OUT_DIR / "operations-gpu.md").write_text(report(result), encoding="utf-8")
    for r in result["paths"]:
        print(f"{'ok' if r['ok'] else 'NG'} {r['path']} {r['video']}: {r['outcome']}")
    for c in result["captures"]:
        print(f"capture {c['file']} {c['size']} min font {c['min_font_px']}")
    print(f"wrote docs/evidence/3b/operations-gpu.md: {'pass' if result['passed'] else 'FAIL'}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
