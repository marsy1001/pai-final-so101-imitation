"""The end-to-end run's evidence: check `runs.jsonl`, render `runs.md`.

    uv run python -m so101_demo.ui.evidence_e2e --check docs/evidence/e2e/runs.jsonl
    uv run python -m so101_demo.ui.evidence_e2e --report docs/evidence/e2e/runs.jsonl

`runs.jsonl` is written by `python -m so101_demo.ui --videos ... --record <dir>`
(`ui.runs_log`), one line per run. `--check` exits 0 only when every line has the required
keys, each of the four (target, destination) combinations has a run that was approved
(承認) and ended `success` with a video, at least one run was stopped as 対応外 (拒否)
with no command sent and no video, every video file exists next to the jsonl, is under
10 MiB and is 1920x1080 at 25 fps (ffprobe), and every line comes from one session on
committed code with the fixed Uc understanding configuration and the expected Cosmos
model. `--report` renders the Markdown
record from the jsonl alone (plus the hash of the fixed Uc configuration it compares
with), so the committed `runs.md` is reproducible byte for byte.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..plan.paths import REPO_ROOT, UC_MANIFEST_PATH
from ..understanding import astra, cosmos
from ..understanding import settings as S
from .runs_log import REQUIRED_KEYS, read_rows, repo_path
from .screen import APPROVE, CORRECT, MANUAL, REJECT

COMBOS = (
    ("red_cube", "box_a"),
    ("red_cube", "box_b"),
    ("blue_cube", "box_a"),
    ("blue_cube", "box_b"),
)
WIDTH, HEIGHT, FPS = 1920, 1080, 25
MAX_MP4_BYTES = 10 * 1024 * 1024  # "10 MB 未満" as `find -size +10M` counts it
UC_CONFIG = REPO_ROOT / "docs" / "evidence" / "uc" / "config-r2.json"
RAN = (APPROVE, CORRECT, MANUAL)

Probe = Callable[[Path], dict[str, Any]]


def probe_mp4(path: Path) -> dict[str, Any]:
    """ffprobe's view of a video: codec, size, frame rate, frame count."""
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
            "-show_entries", "stream=codec_name,width,height,r_frame_rate,nb_read_frames",
            "-of", "json", str(path),
        ],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    v = (json.loads(out.stdout).get("streams") or [{}])[0]
    num, _, den = str(v.get("r_frame_rate", "0/1")).partition("/")
    return {
        "codec": v.get("codec_name"),
        "width": v.get("width"),
        "height": v.get("height"),
        "fps": int(num) / int(den) if int(den or 0) else 0.0,
        "frames": int(v.get("nb_read_frames", 0) or 0),
    }


def server_model(base_url: str = S.COSMOS_BASE_URL) -> str | None:
    """The model the Cosmos server serves; None without a server."""
    import urllib.request

    try:
        with urllib.request.urlopen(base_url.rstrip("/") + "/v1/models", timeout=5) as r:
            return json.loads(r.read())["data"][0]["id"]
    except (OSError, ValueError, KeyError, IndexError):
        return None


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def config_differences(path: Path = UC_CONFIG) -> list[str]:
    """Where the code's understanding configuration differs from the fixed Uc one
    (prompt, request, limits, sampling, Astra instructions and tool). Empty = the same
    instructions as the evaluation of docs/evidence/uc/eval-r2.md."""
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    now = {
        "cosmos.model": S.COSMOS_MODEL,
        "cosmos.prompt": cosmos.SENTENCE_PROMPT,
        "cosmos.reasoning_suffix": cosmos.REASONING_SUFFIX,
        "cosmos.max_tokens": S.COSMOS_MAX_TOKENS,
        "cosmos.timeout_s": S.COSMOS_TIMEOUT_S,
        "cosmos.temperature": S.COSMOS_TEMPERATURE,
        "cosmos.top_p": S.COSMOS_TOP_P,
        "cosmos.top_k": S.COSMOS_TOP_K,
        "cosmos.seed": S.COSMOS_SEED,
        "cosmos.request_template": cosmos.build_request(Path("/VIDEO.mp4")),
        "preprocessing.sample_fps": S.COSMOS_SAMPLE_FPS,
        "astra.timeout_s": S.ASTRA_TIMEOUT_S,
        "astra.instructions": astra.instructions(),
        "astra.tool": astra.submit_plan_tool(),
    }
    diffs = []
    for key, value in now.items():
        section, _, name = key.partition(".")
        fixed = (cfg.get(section) or {}).get(name)
        if json.loads(json.dumps(fixed)) != json.loads(json.dumps(value)):
            diffs.append(key)
    return diffs


def session_info(
    *,
    videos: Path | None,
    ids: list[str] | None,
    machine: Callable[[], dict[str, Any]],
    config: Path = UC_CONFIG,
) -> Callable[[], dict[str, Any]]:
    """What every row of a `--record` session carries (`RunsLog(session=...)`): taken once
    at start, apart from `machine()` (GL renderer and encoder are known after the first
    frame / recording)."""
    started = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
    try:
        commit = _git("rev-parse", "--short", "HEAD")
        dirty = bool(_git("status", "--porcelain", "--", "src", "contracts", "serving"))
    except (OSError, subprocess.CalledProcessError):
        commit, dirty = "unknown", True
    fixed = {
        "started_at": started,
        "commit": commit,
        "uncommitted_code": dirty,
        "cosmos_server_model": server_model(),
        "run_timeout_s": S.RUN_TIMEOUT_S,
        "videos": repo_path(videos.resolve()) if videos else None,
        "ids": list(ids) if ids else None,
        "config": {
            "path": str(config.relative_to(REPO_ROOT)) if config.is_relative_to(REPO_ROOT)
            else str(config),
            "sha256": sha256_file(config) if config.is_file() else None,
            "differences": config_differences(config) if config.is_file() else ["missing"],
        },
    }  # fmt: skip

    def session() -> dict[str, Any]:
        return {**fixed, "machine": machine()}

    return session


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True, timeout=10
    ).stdout.strip()


# --- check ---------------------------------------------------------------------------


def _pair(plan: dict | None) -> tuple[str | None, str | None]:
    return ((plan or {}).get("target"), (plan or {}).get("destination"))


def approved_successes(rows: list[dict]) -> dict[tuple[str, str], list[dict]]:
    """Per combination, the runs approved as shown (承認) that ended `success`."""
    out: dict[tuple[str, str], list[dict]] = {c: [] for c in COMBOS}
    for r in rows:
        if r.get("operation") == APPROVE and r.get("outcome") == "success":
            pair = _pair(r.get("final"))
            if pair in out:
                out[pair].append(r)
    return out


def rejected(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r.get("operation") == REJECT]


def video_scopes(manifest: Path = UC_MANIFEST_PATH) -> dict[str, str]:
    """Video id -> `in_scope` / `out_of_scope` from the acceptance manifest (the gold
    classification; the model's own status is not what decides 対応外)."""
    raw = json.loads(Path(manifest).read_text(encoding="utf-8"))
    return {v["id"]: v["scope"] for v in raw["videos"]}


def out_of_scope_rejections(rows: list[dict], scopes: dict[str, str]) -> list[dict]:
    """拒否 rows whose video the manifest classes as out of scope."""
    return [r for r in rejected(rows) if scopes.get(r.get("video_id")) == "out_of_scope"]


def check(
    rows: list[dict],
    base_dir: Path,
    *,
    probe: Probe | None = probe_mp4,
    manifest: Path = UC_MANIFEST_PATH,
) -> list[str]:
    """Every rule the record breaks (empty = the end-to-end evidence is complete).
    `probe=None` skips ffprobe (the files' presence and size are still checked)."""
    problems: list[str] = []
    if not rows:
        return ["no rows"]
    scopes = video_scopes(manifest)
    for i, r in enumerate(rows, 1):
        missing = [k for k in REQUIRED_KEYS if k not in r]
        if missing:
            problems.append(f"line {i}: missing keys {missing}")
    for combo, found in approved_successes(rows).items():
        if not found:
            problems.append(f"{combo[0]} -> {combo[1]}: no run approved (承認) with `success`")
        elif not any(r.get("mp4") for r in found):
            problems.append(f"{combo[0]} -> {combo[1]}: no approved `success` run has a video")
    rej = rejected(rows)
    if not out_of_scope_rejections(rows, scopes):
        problems.append(
            "no out-of-scope video (manifest scope `out_of_scope`) was stopped as 対応外 (拒否)"
        )
    for r in rows:
        if r.get("video_id") is not None and r.get("video_id") not in scopes:
            problems.append(f"{r.get('run_id')}: video {r['video_id']!r} is not in {manifest.name}")
    for r in rej:
        if r.get("commands_sent"):
            problems.append(f"{r.get('run_id')}: 拒否 but commands_sent = {r['commands_sent']}")
        if r.get("mp4"):
            problems.append(f"{r.get('run_id')}: 拒否 but a video was recorded")
    problems += session_problems(rows)
    for r in rows:
        if r.get("operation") in RAN and r.get("outcome") == "success" and not r.get("mp4"):
            problems.append(f"{r.get('run_id')}: ran with `success` but no video")
        if not r.get("mp4"):
            continue
        path = Path(base_dir) / r["mp4"]
        if not path.is_file():
            problems.append(f"{r.get('run_id')}: video missing: {path}")
            continue
        size = path.stat().st_size
        if size >= MAX_MP4_BYTES:
            problems.append(f"{r.get('run_id')}: {path.name} is {size} bytes (>= 10 MiB)")
        if probe is None:
            continue
        try:
            fmt = probe(path)
        except (OSError, subprocess.SubprocessError, ValueError) as e:
            problems.append(f"{r.get('run_id')}: ffprobe failed on {path.name}: {e}")
            continue
        want = {"width": WIDTH, "height": HEIGHT, "fps": float(FPS)}
        for k, w in want.items():
            if fmt.get(k) != w:
                problems.append(f"{r.get('run_id')}: {path.name} {k} {fmt.get(k)!r} != {w!r}")
    return problems


def session_problems(rows: list[dict]) -> list[str]:
    """One session, committed code, the fixed configuration, the expected Cosmos model."""
    problems = []
    sessions = [r.get("session") or {} for r in rows]
    commits = sorted({str(s.get("commit")) for s in sessions})
    hashes = sorted({str((s.get("config") or {}).get("sha256")) for s in sessions})
    if len(commits) != 1 or len(hashes) != 1:
        problems.append(f"rows from several sessions: commits {commits}, config hashes {hashes}")
    current = sha256_file(UC_CONFIG) if UC_CONFIG.is_file() else None
    for i, s in enumerate(sessions, 1):  # every row: the jsonl is appended to across sessions
        cfg = s.get("config") or {}
        if s.get("uncommitted_code") is not False:
            problems.append(f"line {i}: ran on uncommitted code (session.uncommitted_code)")
        if cfg.get("differences") != []:
            problems.append(
                f"line {i}: the understanding configuration differed from the fixed one: "
                f"{cfg.get('differences')}"
            )
        if cfg.get("sha256") != current:
            problems.append(f"line {i}: session.config.sha256 is not the current {UC_CONFIG.name}")
        if s.get("cosmos_server_model") != S.COSMOS_MODEL:
            problems.append(
                f"line {i}: Cosmos server model {s.get('cosmos_server_model')!r} "
                f"!= {S.COSMOS_MODEL!r}"
            )
    return problems


# --- report --------------------------------------------------------------------------


def _cell(text: Any) -> str:
    return "-" if text is None else str(text).replace("|", "\\|").replace("\n", " ")


def _plan_text(plan: dict | None) -> str:
    if not plan:
        return "-"
    if plan.get("status") == "ok":
        return f"ok / {plan.get('target')} → {plan.get('destination')}"
    return f"{plan.get('status')} / - / -"


def _mb(n: int | None) -> str:
    return "-" if n is None else f"{n / 1e6:.2f} MB"


def _dates(rows: list[dict]) -> list[str]:
    return sorted({str(r.get("at", ""))[:10] for r in rows if r.get("at")})


def _first(rows: list[dict], *keys: str) -> Any:
    for r in rows:
        v: Any = r
        for k in keys:
            v = (v or {}).get(k) if isinstance(v, dict) else None
        if v is not None:
            return v
    return None


def report(rows: list[dict], *, config: Path = UC_CONFIG, manifest: Path = UC_MANIFEST_PATH) -> str:
    """The Markdown record (conclusion first) from the jsonl rows."""
    scopes = video_scopes(manifest)
    per_combo = approved_successes(rows)
    rej = out_of_scope_rejections(rows, scopes)
    other_rej = [r for r in rejected(rows) if r not in rej]
    n_ok = sum(1 for found in per_combo.values() if found)
    videos = [r for r in rows if r.get("mp4")]
    fps = [(r.get("recording") or {}).get("effective_fps") for r in videos]
    slow = [r for r, f in zip(videos, fps, strict=True) if f is not None and f < FPS]
    unmeasured = [r for r, f in zip(videos, fps, strict=True) if f is None]
    sizes = [(r.get("recording") or {}).get("bytes") for r in videos]
    session = _first(rows, "session") or {}
    cfg = session.get("config") or {}
    cfg_now = sha256_file(config) if Path(config).is_file() else None
    cfg_same = cfg.get("sha256") is not None and cfg.get("sha256") == cfg_now
    machine = session.get("machine") or {}
    cosmos_models = sorted(
        {str((r.get("cosmos") or {}).get("model")) for r in rows if r.get("cosmos")}
    )
    astra_models = sorted(
        {str((r.get("astra") or {}).get("model")) for r in rows if r.get("astra")}
    )
    dates = _dates(rows)
    combo_text = "、".join(
        f"{t}→{d}: " + ("、".join(f"{r.get('video_id')}" for r in found) if found else "**なし**")
        for (t, d), found in per_combo.items()
    )
    rej_lines = []
    for r in rej:
        status = (r.get("plan") or {}).get("status")
        line = (
            f"{r.get('video_id')}: 理解層は `{status}`"
            + (
                f"（{(r.get('plan') or {}).get('target')} → "
                f"{(r.get('plan') or {}).get('destination')}）"
                if status == "ok"
                else ""
            )
            + f"、「対応外にする」で停止、送った指令 {r.get('commands_sent', 0)}"
        )
        if status == "ok":
            line += "。**理解層は対応外の動画に `ok` を返した**（問い 2 の失敗の型として報告に回す。指示文は直していない）"
        rej_lines.append(line)
    other_text = (
        "。対応内の動画を「対応外にする」で止めた run: "
        + "、".join(f"{r.get('video_id')}（`{r.get('run_id')}`）" for r in other_rej)
        if other_rej
        else ""
    )
    timeout_s = session.get("run_timeout_s")
    lines = [
        "# 通し実行の記録: 動画 → 理解 → 承認 → sim 実行 → 録画",
        "",
        "`uv run python -m so101_demo.ui.evidence_e2e --report docs/evidence/e2e/runs.jsonl` が "
        "`runs.jsonl` から生成（1 run 1 行。`python -m so101_demo.ui --videos ... --record` が書く）。",
        "",
        "## 結論",
        "",
        f"- 対応内 4 組合せ: 承認（「これで動かす」）→ runner `success` の run がある組合せ **{n_ok}/4**"
        f"（{combo_text}）",
        f"- 対応外（manifest `{manifest.name}` の scope が `out_of_scope` の動画）: 「対応外にする」で"
        f"止めた run **{len(rej)} 本**"
        + ("。" + "；".join(rej_lines) if rej_lines else "（**なし**）")
        + other_text,
        f"- 録画: {len(videos)} 本、"
        + (
            f"{_mb(min(s for s in sizes if s is not None))} 〜 {_mb(max(s for s in sizes if s is not None))}、"
            if any(s is not None for s in sizes)
            else ""
        )
        + (
            f"実効 fps {min(f for f in fps if f is not None):.1f} 〜 {max(f for f in fps if f is not None):.1f}"
            if any(f is not None for f in fps)
            else "実効 fps 未計測"
        )
        + (
            "（25 fps を割った run: "
            + "、".join(str(r.get("run_id")) for r in slow)
            + "。失敗にはしない）"
            if slow
            else "（25 fps を割った run なし）"
        )
        + (
            "（fps 計測不能（2 フレーム未満）: "
            + "、".join(str(r.get("run_id")) for r in unmeasured)
            + "）"
            if unmeasured
            else ""
        ),
        f"- 実行日（UTC）: {'、'.join(dates) or '-'}、commit `{session.get('commit', '-')}`"
        + ("（**コードに未コミットの変更あり**）" if session.get("uncommitted_code") else ""),
        f"- 理解層: Cosmos `{'、'.join(cosmos_models) or '-'}`（サーバー報告 "
        f"`{session.get('cosmos_server_model') or '-'}`）、Astra（API 報告のモデル）"
        f"`{'、'.join(astra_models) or '-'}`",
        f"- 構成: `{cfg.get('path', '-')}` の sha256 `{cfg.get('sha256') or '-'}`（実行時に記録）。"
        f"現在のファイルと**{'一致' if cfg_same else '不一致'}**。コードの指示文・要求・上限との差分: "
        + (
            "**あり** " + "、".join(cfg.get("differences"))
            if cfg.get("differences")
            else "なし（Uc 改訂 2 と同じ指示文）"
        ),
        f"- 読み取りの待機上限 `RUN_TIMEOUT_S` = {timeout_s if timeout_s is not None else '-'} 秒",
        "",
        "## 条件",
        "",
        "| 項目 | 値 |",
        "|---|---|",
        f"| 起動 | `MUJOCO_GL=egl uv run python -m so101_demo.ui --videos {_cell(session.get('videos'))}"
        + (f" --ids {','.join(session.get('ids'))}" if session.get("ids") else "")
        + " --record docs/evidence/e2e` |",
        f"| 端末 | GPU {_cell(machine.get('gpu'))}、GL `{_cell(machine.get('gl'))}`、"
        f"encoder `{_cell(machine.get('encoder'))}` |",
        f"| 録画 | {WIDTH}×{HEIGHT}、{FPS} fps（sim 時間）、実行中の区間のみ、ラベル付き（`evidence/record.py`、`evidence/overlay.py`） |",
        "| 承認 | 画面の操作のみ（`approve_from_review`）。自動承認は無い |",
        "",
        "## run ごとの結果",
        "",
        "| # | run_id | 動画 | 理解層の結果（status / target → destination） | 段の失敗 | 操作 | runner の結果 "
        "| 指令数 | 動画ファイル | サイズ | 実効 fps | 所要 s（理解 / 実行） |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(rows, 1):
        rec = r.get("recording") or {}
        el = r.get("elapsed_s") or {}
        lines.append(
            f"| {i} | `{_cell(r.get('run_id'))}` | {_cell(r.get('video_id'))} | "
            f"{_plan_text(r.get('plan'))} | {_cell(r.get('failed_stage'))} | "
            f"{_cell(r.get('operation'))}{'（救済）' if r.get('rescued') else ''} | "
            f"{_cell(r.get('outcome'))} | {_cell(r.get('commands_sent'))} | "
            f"{'`' + _cell(r.get('mp4')) + '`' if r.get('mp4') else '-'} | {_mb(rec.get('bytes'))} | "
            f"{'-' if rec.get('effective_fps') is None else f'{rec["effective_fps"]:.1f}'} | "
            f"{_cell(el.get('understanding'))} / {_cell(el.get('run'))} |"
        )
    lines += ["", "## Cosmos の一文と Astra のプラン", ""]
    for i, r in enumerate(rows, 1):
        plan = r.get("plan") or {}
        lines += [
            f"### {i}. {_cell(r.get('video_id'))}（`{_cell(r.get('run_id'))}`、{_cell(r.get('at'))}）",
            "",
            f"> {_cell(r.get('sentence')) if r.get('sentence') else '（一文なし: ' + _cell((r.get('cosmos') or {}).get('failure') or r.get('source_error')) + '）'}",
            "",
            f"- プラン: {_plan_text(r.get('plan'))}、note: {_cell(plan.get('note'))}",
            f"- Astra の生 arguments: `{_cell((r.get('astra') or {}).get('raw_arguments'))}`",
            f"- 画面に出した案: {_plan_text(r.get('shown'))}、実行したプラン: {_plan_text(r.get('final'))}",
            f"- Cosmos {_cell((r.get('cosmos') or {}).get('elapsed_s'))} s"
            f"（finish `{_cell((r.get('cosmos') or {}).get('finish_reason'))}`）、"
            f"Astra {_cell((r.get('astra') or {}).get('elapsed_s'))} s",
            "",
        ]
    lines += [
        "## 出典",
        "",
        "- `docs/evidence/e2e/runs.jsonl`（この記録の元。`uv run python -m so101_demo.ui.evidence_e2e --check` が検査する）",
        "- `docs/evidence/uc/eval-r2.md`、`docs/evidence/uc/config-r2.json`（理解層の固定構成と精度）",
        "- `docs/approval-screen.md`（起動と操作）、`docs/execution-runner.md`（承認の経路と判定）",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", type=Path, metavar="RUNS_JSONL")
    g.add_argument("--report", type=Path, metavar="RUNS_JSONL")
    args = ap.parse_args(argv)
    path = args.check or args.report
    rows = read_rows(path)
    if args.report:
        sys.stdout.write(report(rows))
        return 0
    problems = check(rows, path.parent)
    for p in problems:
        print(p)
    if not problems:
        print(f"runs OK: {len(rows)} runs, 4 combinations approved and recorded, 対応外 stopped")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
