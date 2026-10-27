"""Uc evaluation: fix the configuration, run the 15 acceptance videos, report G2.

    uv run python -m so101_demo.evidence.uc_eval --fix-config   # write config.json (commit it)
    uv run python -m so101_demo.evidence.uc_eval --run          # GPU machine: eval.json + eval.md
    uv run python -m so101_demo.evidence.uc_eval --report-only  # rebuild eval.md from the json

The configuration is fixed before the evaluation: `--fix-config` collects every value the
run depends on (Cosmos instruction and request, what the video processor does to each
video, token and time limits, seed, Astra instructions and tool, model and server
revisions) into docs/evidence/uc/config.json. `--run` rebuilds the same dict from the code
and refuses to start when it differs from the committed file, when the file is not
committed, or when src/, contracts/ or serving/ have uncommitted changes. It sends one
warm-up request on a development video (s1-01, never an acceptance video), then every
acceptance video once, and keeps every row (failures and timeouts included).

The report is rendered only from files in the repository (eval.json, config.json,
human-check.json, the manifest), so `--report-only` reproduces the committed eval.md
byte for byte. The hashes of config.json and the manifest are taken at run time and
compared with the current files in the report.

A revision (amended 2026-09-29: no limit on the number) gets its own files: config-r2.json,
eval-r2.json, eval-r2.md, human-check-r2.json. The first revision's files are never rewritten
by a later one.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from ..plan.paths import REPO_ROOT, S1_MANIFEST_PATH, UC_MANIFEST_PATH
from ..understanding import astra, cosmos, pipeline
from ..understanding import settings as S
from ..understanding.transport import load_env
from ..understanding.videos import sha256_file
from . import uc_eval
from .s1_diagnosis import _cosmos_row, _plan_dict, _probe, _server_model

OUT_DIR = REPO_ROOT / "docs" / "evidence" / "uc"
SERVING_DIR = REPO_ROOT / "serving" / "cosmos"
HF_SNAPSHOTS = (
    Path.home()
    / ".cache"
    / "huggingface"
    / "hub"
    / "models--nvidia--Cosmos-Reason2-2B"
    / "snapshots"
)
VIDEO_PLACEHOLDER = Path("/VIDEO.mp4")


TUNING_RULES = {
    1: "The Cosmos instruction and the preprocessing were chosen on the development set only "
    "(S1, docs/evidence/s1/diagnosis.md 「プロンプトの比較」); the acceptance videos uc-* are "
    "run only with this fixed configuration.",
    2: "Chosen after looking at the acceptance results of the earlier revisions (rule amended "
    "2026-09-29 by the user: the development set cannot tell prompts apart). The "
    "acceptance score of this revision is therefore tuned on the acceptance videos and can be "
    "higher than on videos seen for the first time; revision 1 is the untuned score.",
}


def revisions() -> list[int]:
    """Revisions with a fixed configuration, in order."""
    found = [1] if (OUT_DIR / "config.json").is_file() else []
    for p in OUT_DIR.glob("config-r*.json"):
        found.append(int(p.stem.removeprefix("config-r")))
    return sorted(found)


def _suffix(rev: int) -> str:
    return "" if rev == 1 else f"-r{rev}"


def paths(rev: int = 1) -> dict[str, Path]:
    s = _suffix(rev)
    return {
        "config": OUT_DIR / f"config{s}.json",
        "eval": OUT_DIR / f"eval{s}.json",
        "report": OUT_DIR / f"eval{s}.md",
        "human": OUT_DIR / f"human-check{s}.json",
    }


def _rel(p: Path) -> str:
    return str(p.relative_to(REPO_ROOT))


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()


def _locked_versions(names: tuple[str, ...]) -> dict[str, str]:
    """Package versions pinned by serving/cosmos/uv.lock (the server's environment)."""
    text = (SERVING_DIR / "uv.lock").read_text(encoding="utf-8")
    out = {}
    for name in names:
        m = re.search(rf'^name = "{re.escape(name)}"\nversion = "([^"]+)"', text, re.M)
        out[name] = m.group(1) if m else None
    return out


def _model_revision() -> str | None:
    snaps = sorted(p.name for p in HF_SNAPSHOTS.glob("*")) if HF_SNAPSHOTS.is_dir() else []
    return snaps[0] if len(snaps) == 1 else None


def _dev_videos() -> list[tuple[str, Path]]:
    raw = json.loads(S1_MANIFEST_PATH.read_text(encoding="utf-8"))
    return [(v["id"], REPO_ROOT / v["path"]) for v in raw["videos"]]


ASTRA_MODEL = "gpt-6-astra"  # the model the API reported in S1 (docs/evidence/s1/)


def _sha256_text(text: str | None) -> str | None:
    return hashlib.sha256(text.encode()).hexdigest() if text else None


def build_config(
    rev: int = 1, *, probe=_probe, env: dict[str, str] | None = None
) -> dict[str, Any]:
    """Every value the evaluation depends on, from the code and the environment.

    The Astra endpoint and deployment come from .env; only their sha256 is kept (the names
    stay out of the repository), so changing either after the fix stops `--run`.
    """
    env = load_env() if env is None else env
    raw, videos = uc_eval.load_manifest()
    probes = {vid: probe(path) for vid, path in [*_dev_videos(), *((v.id, v.path) for v in videos)]}
    keep = ("source_frames", "sampled_frames", "frame_hw", "video_tokens", "error")
    frames = {vid: {k: p[k] for k in keep if k in p} for vid, p in probes.items()}
    budgets = {json.dumps(p.get("pixel_budget"), sort_keys=True) for p in probes.values()}
    return {
        "revision": rev,
        "tuning": {
            "development_set": raw["development_set"]["ids"],
            "rule": TUNING_RULES[min(rev, 2)],
        },
        "cosmos": {
            "model": S.COSMOS_MODEL,
            "model_revision": _model_revision(),
            "server": {
                **_locked_versions(("vllm", "torch", "transformers")),
                "serve_sh_sha256": sha256_file(SERVING_DIR / "serve.sh"),
                "uv_lock_sha256": sha256_file(SERVING_DIR / "uv.lock"),
            },
            "prompt": cosmos.SENTENCE_PROMPT,
            "reasoning_suffix": cosmos.REASONING_SUFFIX,
            "max_tokens": S.COSMOS_MAX_TOKENS,
            "timeout_s": S.COSMOS_TIMEOUT_S,
            "temperature": S.COSMOS_TEMPERATURE,
            "top_p": S.COSMOS_TOP_P,
            "top_k": S.COSMOS_TOP_K,
            "seed": S.COSMOS_SEED,
            "request_template": cosmos.build_request(VIDEO_PLACEHOLDER),
        },
        "preprocessing": {
            "version": "S1 adopted version: the cut mp4 (1280x720, 24 fps, 240 frames) goes to "
            "the server as a file:// URL unchanged; the model's video processor samples and "
            "resizes it (serving/cosmos/probe_video.py)",
            "sample_fps": S.COSMOS_SAMPLE_FPS,
            # one budget for every video, or null when they disagree (fix_config refuses)
            "pixel_budget": json.loads(budgets.pop()) if len(budgets) == 1 else None,
            "frames": frames,
        },
        "astra": {
            "model_reported_in_s1": ASTRA_MODEL,
            "endpoint_sha256": _sha256_text(env.get("AOAI_ENDPOINT")),
            "deployment_sha256": _sha256_text(env.get("AOAI_ASTRA_DEPLOYMENT")),
            "timeout_s": S.ASTRA_TIMEOUT_S,
            "instructions": astra.instructions(),
            "tool": astra.submit_plan_tool(),
        },
    }


def unfixable(cfg: dict[str, Any]) -> list[str]:
    """Why a built configuration cannot be fixed (empty = it can)."""
    reasons = []
    if bad := [k for k, f in cfg["preprocessing"]["frames"].items() if "error" in f]:
        reasons.append(f"probe errors {bad}")
    if cfg["cosmos"]["model_revision"] is None:
        reasons.append("no Cosmos model revision")
    if cfg["preprocessing"]["pixel_budget"] is None:
        reasons.append("no single pixel budget")
    for key, name in (
        ("endpoint_sha256", "AOAI_ENDPOINT"),
        ("deployment_sha256", "AOAI_ASTRA_DEPLOYMENT"),
    ):
        if cfg["astra"][key] is None:
            reasons.append(f"no {name} in .env")
    return reasons


def fix_config(rev: int = 1) -> Path:
    path = paths(rev)["config"]
    if path.exists():
        raise SystemExit(f"{_rel(path)} exists; a fixed configuration is never rewritten")
    cfg = build_config(rev)
    if reasons := unfixable(cfg):
        raise SystemExit("cannot fix the configuration: " + "; ".join(reasons))
    cfg = {"fixed": dt.date.today().isoformat(), **cfg}
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def config_problems(rev: int = 1) -> list[str]:
    """Why the evaluation must not start with the committed configuration (empty = go)."""
    path = paths(rev)["config"]
    if not path.is_file():
        return [f"{_rel(path)} missing; run --fix-config and commit it"]
    problems = []
    if _git("status", "--porcelain", "--", _rel(path), "src", "contracts", "serving"):
        problems.append("uncommitted changes in the config, src/, contracts/ or serving/")
    fixed = json.loads(path.read_text(encoding="utf-8"))
    fixed.pop("fixed", None)
    now = json.loads(json.dumps(build_config(rev)))
    for key in sorted(set(fixed) | set(now)):
        if fixed.get(key) != now.get(key):
            problems.append(f"config.json differs from the code / environment in {key!r}")
    return problems


def run(rev: int = 1) -> dict[str, Any]:
    p = paths(rev)
    if p["eval"].exists():
        raise SystemExit(f"{_rel(p['eval'])} exists; results are never overwritten")
    model = _server_model(S.COSMOS_BASE_URL)
    if model is None:
        raise SystemExit(f"no Cosmos server at {S.COSMOS_BASE_URL}; start serving/cosmos/serve.sh")
    if problems := uc_eval.check_manifest():
        raise SystemExit("manifest is not valid: " + "; ".join(problems))
    if problems := config_problems(rev):
        raise SystemExit("configuration not fixed: " + "; ".join(problems))
    raw, videos = uc_eval.load_manifest()
    print("warm-up (s1-01, development set) ...")
    warm = cosmos.describe(_dev_videos()[0][1])
    rows = []
    for v in videos:
        u = pipeline.understand(v.path)
        a = u.astra
        rows.append(
            {
                "video": v.id,
                "run": 1,
                "cosmos": _cosmos_row(u.cosmos),
                "astra": None
                if a is None
                else {
                    "api_status": a.api_status,
                    "failure": a.failure,
                    "raw_arguments": a.raw_arguments,
                    "contract_valid": a.failure is None,
                    "elapsed_s": round(a.elapsed_s, 2),
                    "model": a.model,
                    "response_id": a.response_id,
                    "usage": a.usage,
                },
                "failed_stage": u.failed_stage,
                "plan": _plan_dict(u.plan),
                "executable": u.plan.executable,
                "total_s": round(u.elapsed_s, 2),
            }
        )
        r = rows[-1]
        print(
            f"{v.id}: cosmos {r['cosmos']['elapsed_s']} s {r['cosmos']['finish_reason']} -> "
            f"{u.plan.status} {u.plan.target} {u.plan.destination} ({r['total_s']} s) "
            f"{u.failed_stage or ''}"
        )
    data = {
        "date": dt.date.today().isoformat(),
        "revision": rev,
        "commit": _git("rev-parse", "HEAD"),
        "config_commit": _git("log", "-1", "--format=%H", "--", _rel(p["config"])),
        "config_sha256": sha256_file(p["config"]),
        "manifest_version": raw["version"],
        "manifest_sha256": sha256_file(UC_MANIFEST_PATH),
        "cosmos_server_model": model,
        "warmup": {"video": "s1-01", **_cosmos_row(warm)},
        "rows": rows,
    }
    p["eval"].write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return data


# --- report --------------------------------------------------------------------------


def _mark(ok: bool) -> str:
    return "合格" if ok else "不合格"


def _cell(text: Any) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def render(rev: int = 1) -> str:
    p = paths(rev)
    data = json.loads(p["eval"].read_text(encoding="utf-8"))
    cfg = json.loads(p["config"].read_text(encoding="utf-8"))
    human = json.loads(p["human"].read_text(encoding="utf-8")) if p["human"].is_file() else {}
    confirmed = bool(human.get("user_confirmed"))
    checks = human.get("videos", {}) if confirmed else {}
    _, videos = uc_eval.load_manifest()
    rows = {r["video"]: r for r in data["rows"]}
    sc = uc_eval.score(data["rows"], videos, checks)
    lat = sc["latency_s"]
    fails = sc["in_scope_failures"]
    facts = sc["cosmos_facts"]
    faithful = [
        vid
        for vid, c in checks.items()
        if c.get("astra_faithful") is True and uc_eval._checked(c, _sentence(rows.get(vid)))
    ]
    unfaithful = [
        vid
        for vid, c in checks.items()
        if c.get("astra_faithful") is False and uc_eval._checked(c, _sentence(rows.get(vid)))
    ]
    astra_called = [r for r in data["rows"] if r["astra"]]
    want_model = cfg["astra"]["model_reported_in_s1"]
    same_model = sum(r["astra"]["model"] == want_model for r in astra_called)
    finish = [r["cosmos"]["finish_reason"] for r in data["rows"]]
    out_fail = [
        f"{v.id}（{rows[v.id]['failed_stage']}: "
        f"{(rows[v.id]['cosmos'] or {}).get('failure') or (rows[v.id]['astra'] or {}).get('failure')}）"
        for v in videos
        if v.scope == "out_of_scope" and v.id in rows and rows[v.id]["failed_stage"]
    ]
    cfg_now = sha256_file(p["config"])
    man_now = sha256_file(UC_MANIFEST_PATH)
    hashes_ok = cfg_now == data["config_sha256"] and man_now == data["manifest_sha256"]
    passed = sc["g2_accuracy_pass"] and sc["g2_latency_pass"]
    c = cfg["cosmos"]
    title_rev = "" if rev == 1 else f"（改訂 {rev}）"

    lines = [
        f"# Uc 評価{title_rev}: 受け入れ動画 15 本で G2 の理解層を判定",
        "",
        "`uv run python -m so101_demo.evidence.uc_eval --report-only` が "
        f"`{_rel(p['eval'])}`・`{_rel(p['config'])}`・`{_rel(p['human'])}` から生成。",
        "",
        "## 結論",
        f"- **G2 理解層: {_mark(passed)}**（精度 {_mark(sc['g2_accuracy_pass'])}、"
        f"所要 {_mark(sc['g2_latency_pass'])}）。構成リビジョン {rev}、manifest version "
        f"{data['manifest_version']}、実行日 {data['date']}",
        f"- 対応内の完全一致: **{sc['in_scope_exact']}/{sc['in_scope_total']}**"
        f"（基準 {uc_eval.G2_IN_SCOPE_MIN} 以上）",
        "- 4 組合せそれぞれ（基準 各 "
        f"{uc_eval.G2_PER_COMBO_MIN}/{sc['per_combo_total']} 以上）: "
        + "、".join(f"{k} **{n}/{sc['per_combo_total']}**" for k, n in sc["per_combo"].items()),
        f"- 対応外で実行可能プランを返した数: **{sc['out_of_scope_false_accepts']}"
        f"/{sc['out_of_scope_total']}**（基準 0）"
        + (
            f"（{', '.join(sc['out_of_scope_false_accept_ids'])}）"
            if sc["out_of_scope_false_accepts"]
            else ""
        ),
        "- 対応内の失敗の内訳: "
        + f"スキーマ不正 {fails['schema_invalid']}、タイムアウト {fails['timeout']}、"
        f"対応内への拒否 {fails['refused']}、誤ったプラン {fails['wrong_plan']}、"
        f"その他のエラー {fails['other_error']}、行なし {fails['missing']}"
        + (f"。対応外の段の失敗: {'、'.join(out_fail)}" if out_fail else ""),
        f"- 所要（動画投入 〜 検証済み最終 JSON、{lat['n']} 本）: 中央値 **{lat['median']:.1f} 秒**、"
        f"最大 **{lat['max']:.1f} 秒**（基準 {uc_eval.G2_MEDIAN_S:.0f} 秒 / {uc_eval.G2_MAX_S:.0f} 秒）",
        "- 診断値（合否には使わない）: Cosmos の事実一致 "
        + (
            f"**{facts['match']}/{facts['sentences']}**（不一致 {facts['mismatch']}、"
            f"未照合 {len(facts['unchecked'])}）、Astra の文への忠実性 "
            f"**{len(faithful)}/{len(astra_called)}**（不忠実 {len(unfaithful)}）"
            if confirmed
            else "人の照合がユーザー確認待ちのため、事実一致・忠実性は数えていない（全文を未照合として扱う）"
        ),
        f"- Cosmos の `finish_reason`: stop {finish.count('stop')}/{len(finish)}"
        "（stop 以外は失敗として数えた）",
        f"- 構成を固定したコミット: `{data['config_commit']}`（評価の実行時の HEAD: `{data['commit']}`）",
        f"- Astra のモデル: API が報告したモデルが `{want_model}` だった行 **{same_model}/{len(astra_called)}**"
        + (
            "。deployment は config に sha256 で固定"
            if "deployment_sha256" in cfg["astra"]
            else "。この改訂の config は deployment を記録していない（レビュー指摘）ため、"
            "各行の API 報告モデルで照合した"
        ),
        f"- 評価後の変更の照合: config の sha256 `{data['config_sha256']}`、manifest の sha256 "
        f"`{data['manifest_sha256']}`（評価時に記録）。現在のファイルと"
        f"**{'一致' if hashes_ok else '不一致'}**",
        "",
        "## 動画ごとの結果",
        "| id | 範囲 | gold | 結果 status / target / destination | 実行可能 | 判定 | Cosmos (s) "
        "| finish | Astra (s) | 合計 (s) | 事実一致 | Astra 忠実 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for v in videos:
        r = rows.get(v.id)
        if r is None:
            lines.append(
                f"| {v.id} | {v.scope} | — | 行なし | — | 失敗（missing） | — | — | — | — | — | — |"
            )
            continue
        g, pl = v.gold, r["plan"]
        if v.scope == "in_scope":
            pv = sc["per_video"][v.id]
            verdict = "完全一致" if pv["exact"] else f"失敗（{pv['failure']}）"
        else:
            verdict = "実行可能を返した（誤受理）" if r["executable"] else "実行不可（正）"
        chk = checks.get(v.id)
        checked = uc_eval._checked(chk, _sentence(r))
        fact = ("○" if chk["facts_match"] else "×") if checked else "未照合"
        faith = (
            ("○" if chk.get("astra_faithful") else "×")
            if checked and r["astra"] and "astra_faithful" in chk
            else "—"
        )
        kind = f"（{v.out_of_scope_kind}）" if v.out_of_scope_kind else ""
        astra_s = f"{r['astra']['elapsed_s']:.2f}" if r["astra"] else "—"
        lines.append(
            f"| {v.id} | {v.scope}{kind} "
            f"| {g['status']} / {g.get('target')} / {g.get('destination')} "
            f"| {pl['status']} / {pl['target']} / {pl['destination']} "
            f"| {'yes' if r['executable'] else 'no'} | {verdict} "
            f"| {r['cosmos']['elapsed_s']:.2f} | {r['cosmos']['finish_reason'] or '—'} "
            f"| {astra_s} | {r['total_s']:.2f} | {fact} | {faith} |"
        )
    lines += ["", "## Cosmos の一文と人の照合", ""]
    lines.append(
        f"照合は `{p['human'].name}`（1 fps のフレーム一覧 `docs/evidence/uc/frames/` と manifest の"
        "gold の事実で手で行った。Astra を採点者にしない）。"
        + (
            "ユーザー確認済み。"
            if confirmed
            else "**ユーザー確認待ち**（確認までは未照合として扱う）。"
        )
    )
    lines.append("")
    for v in videos:
        r = rows.get(v.id)
        if r is None:
            continue
        chk = checks.get(v.id)
        checked = uc_eval._checked(chk, _sentence(r))
        lines += [
            f"### {v.id}",
            f"> {_cell(r['cosmos']['sentence'] or '(なし: ' + str(r['cosmos']['failure']) + ')')}",
            "",
            f"- Astra の生 arguments: `{r['astra']['raw_arguments'] if r['astra'] else '(呼んでいない)'}`",
        ]
        if checked:
            lines += [
                f"- 事実: **{chk['verdict']}**。{chk['detail']}",
                f"- Astra の忠実性: {chk.get('astra_note', '—')}",
            ]
        else:
            lines.append("- 未照合")
        lines.append("")
    lines += [
        "## 固定した構成（`" + _rel(p["config"]) + "`）",
        f"- Cosmos: `{c['model']}` revision `{c['model_revision']}`、vLLM {c['server']['vllm']}"
        f"（torch {c['server']['torch']}、transformers {c['server']['transformers']}、"
        "`serving/cosmos/serve.sh`）。サーバーの報告値: " + f"`{data['cosmos_server_model']}`",
        f"- 指示文: {c['prompt']}（+ 公式の推論用接尾辞）",
        f"- `max_tokens` {c['max_tokens']}、タイムアウト {c['timeout_s']:.0f} 秒、temperature "
        f"{c['temperature']}、top_p {c['top_p']}、top_k {c['top_k']}、seed {c['seed']}",
        f"- 前処理: {cfg['preprocessing']['version']}。sample fps {cfg['preprocessing']['sample_fps']:g}、"
        f"画素予算 {cfg['preprocessing']['pixel_budget']}、実フレーム数: "
        + "、".join(
            f"{k} {f['sampled_frames']}×{f['frame_hw'][1]}×{f['frame_hw'][0]}"
            for k, f in cfg["preprocessing"]["frames"].items()
        ),
        f"- Astra: 待機上限 {cfg['astra']['timeout_s']:.0f} 秒、instructions と tool は config の全文。"
        "API の報告したモデル: "
        + ", ".join(sorted({r["astra"]["model"] for r in astra_called if r["astra"]["model"]})),
        (
            f"- 調整に使った動画: {', '.join(cfg['tuning']['development_set'])} のみ"
            "（受け入れ用 15 本は使っていない）"
            if rev == 1
            else "- 調整: 受け入れ用 15 本の前の版の結果を見て指示文を直した（2026-09-29）。"
            "この版の点数は受け入れ用に合わせ込んだ後の値で、初見の動画では"
            "下がりうる。合わせ込む前の点数は改訂 1（`eval.md`）"
        ),
        f"- ウォームアップ: {data['warmup']['video']}（開発用）で 1 回、{data['warmup']['elapsed_s']:.1f} 秒。計測から外した",
        "",
        "## 条件",
        "- 採点: `src/so101_demo/evidence/uc_eval.py` の `score`。対応内は status ok で"
        "target・destination・steps が gold と一致したものだけを完全一致とし、段の失敗は失敗の種類で数える",
        "- 所要: `pipeline.understand` の動画投入 〜 検証済み最終 JSON。Cosmos / Astra は各リクエストの送信 〜 応答受信",
        "- API へ送ったのは一文と登録 ID だけ（R7）。動画とフレームは送っていない",
        "",
        "## 出典",
        "- `docs/brief.md`「問い・スコープ」問い 2（理解層の精度と失敗の型）",
        "- `contracts/fixtures/uc/videos.json`（動画、gold）",
        "- `docs/evidence/s1/diagnosis.md`（指示文の比較と運用値）、`docs/evidence/s1/serving.md`",
    ]
    return "\n".join(lines) + "\n"


def _sentence(row: dict[str, Any] | None) -> str | None:
    return ((row or {}).get("cosmos") or {}).get("sentence")


def write_reports() -> list[Path]:
    out = []
    for rev in revisions():
        p = paths(rev)
        if p["eval"].is_file():
            p["report"].write_text(render(rev), encoding="utf-8")
            out.append(p["report"])
    return out
