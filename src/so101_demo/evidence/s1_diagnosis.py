"""S1 diagnosis evidence (Issue #22): the full video run, the manifest check, the Astra round trip.

    uv run python -m so101_demo.evidence.s1_diagnosis             # full run (GPU machine)
    uv run python -m so101_demo.evidence.s1_diagnosis --render    # rebuild diagnosis.md only
    uv run python -m so101_demo.evidence.s1_diagnosis --check-manifest
    uv run python -m so101_demo.evidence.s1_diagnosis --astra-roundtrip

The full run needs the Cosmos server (serving/cosmos/serve.sh, docs/evidence/s1/serving.md)
and the Astra keys in .env. It sends one warm-up request, then runs every manifest video
through Cosmos -> Astra -> contract validation `RUNS` times, and writes
docs/evidence/s1/diagnosis.json (raw) and diagnosis.md. What the video processor does to
each video (sampled frames, frame size, pixel budget) comes from
serving/cosmos/probe_video.py, run in the serving environment. The human check of each
sentence against gold is written by hand in docs/evidence/s1/human-check.json and merged
into diagnosis.md by `--render`, so a rerun never overwrites it.

`--check-manifest` checks contracts/fixtures/s1/videos.json against the committed videos
(hashes, breakdown, gold plans; `understanding.videos.check_manifest`) and exits 0 only
when nothing is wrong. `--astra-roundtrip` sends the human-written sentences below to the
real Astra deployment (AOAI_* in .env) and writes docs/evidence/s1/astra-roundtrip.md
(+ .json): the sentence, the raw tool call arguments, the response status, the contract
validation result and the elapsed time. It tests the sentence -> JSON stage alone; the
expected status per sentence is written here before the run, and Astra never judges its
own output."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics
import subprocess
import sys

from ..plan.paths import REPO_ROOT
from ..understanding import astra, cosmos, pipeline
from ..understanding import settings as S
from ..understanding.videos import check_manifest, load_manifest

OUT_DIR = REPO_ROOT / "docs" / "evidence" / "s1"

# (id, kind, sentence, expected status, expected target, expected destination)
ROUNDTRIP_SENTENCES = [
    (
        "A1",
        "正しい文",
        "A hand picks up the red cube and puts it into the tray on the left side of the image.",
        "ok",
        "red_cube",
        "box_a",
    ),
    (
        "A2",
        "正しい文",
        "The person grabs the blue cube, carries it across the table and drops it into the "
        "container on the right.",
        "ok",
        "blue_cube",
        "box_b",
    ),
    (
        "A3",
        "対応外（未登録物体）",
        "A hand puts a black pen into the tray on the left side of the image.",
        "unsupported",
        None,
        None,
    ),
    (
        "A4",
        "対応外（未登録動作）",
        "A hand lifts the red cube and puts it back where it was; nothing is put into a tray.",
        "unsupported",
        None,
        None,
    ),
    (
        "A5",
        "置き先が欠けた文",
        "A hand picks up the blue cube and carries it out of view.",
        "needs_review",
        "blue_cube",
        None,
    ),
    (
        "A6",
        "対象が欠けた文",
        "A hand puts something into the tray on the right, but the object is hidden by the hand.",
        "needs_review",
        None,
        "box_b",
    ),
    (
        "A7",
        "曖昧な文",
        "A hand puts a cube into one of the trays.",
        "needs_review",
        None,
        None,
    ),
]


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()


def _git_head() -> str:
    """Short HEAD, marked when the working tree has uncommitted changes to the code."""
    try:
        head = _git("rev-parse", "--short", "HEAD")
        dirty = _git("status", "--porcelain", "--", "src", "contracts")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return f"{head} + uncommitted changes" if dirty else head


def run_roundtrip() -> dict:
    rows = []
    for sid, kind, sentence, exp_status, exp_target, exp_dest in ROUNDTRIP_SENTENCES:
        r = astra.structure(sentence)
        p = r.plan
        rows.append(
            {
                "id": sid,
                "kind": kind,
                "sentence": sentence,
                "expected": {"status": exp_status, "target": exp_target, "destination": exp_dest},
                "api_status": r.api_status,
                "failure": r.failure,
                "raw_arguments": r.raw_arguments,
                "contract_valid": r.failure is None,
                "plan": {
                    "status": p.status,
                    "target": p.target,
                    "destination": p.destination,
                    "steps": [{k: v for k, v in vars(s).items() if v is not None} for s in p.steps],
                    "note": p.note,
                },
                "executable": p.executable,
                "matches_expected": (p.status, p.target, p.destination)
                == (exp_status, exp_target, exp_dest),
                "elapsed_s": round(r.elapsed_s, 2),
                "response_id": r.response_id,
                "model": r.model,
                "usage": r.usage,
            }
        )
        print(
            f"{sid}: {p.status} {p.target} {p.destination} ({r.elapsed_s:.1f} s) {r.failure or ''}"
        )
    return {
        "date": dt.date.today().isoformat(),
        "commit": _git_head(),
        "endpoint_path": "/openai/v1/responses (Azure OpenAI, v1 API)",
        "timeout_s": S.ASTRA_TIMEOUT_S,
        "request_template": astra.build_request("<sentence>", "<AOAI_ASTRA_DEPLOYMENT>"),
        "rows": rows,
    }


def write_roundtrip(data: dict) -> None:
    rows = data["rows"]
    n = len(rows)
    completed = sum(r["api_status"] == "completed" for r in rows)
    valid = sum(r["contract_valid"] for r in rows)
    matched = sum(r["matches_expected"] for r in rows)
    false_exec = [r["id"] for r in rows if r["executable"] and r["expected"]["status"] != "ok"]
    mismatches = [
        f"{r['id']}（期待 {r['expected']['status']} / {r['expected']['target']} / "
        f"{r['expected']['destination']} → {r['plan']['status']} / {r['plan']['target']} / "
        f"{r['plan']['destination']}、実行{'可能' if r['executable'] else '不可'}）"
        for r in rows
        if not r["matches_expected"]
    ]
    times = [r["elapsed_s"] for r in rows]
    models = sorted({r["model"] for r in rows if r["model"]})
    lines = [
        "# S1: Astra 往復の実測（人が書いた文 → submit_plan → 契約 schema 検証）",
        "",
        "`uv run python -m so101_demo.evidence.s1_diagnosis --astra-roundtrip` が生成（Issue #22 XPS 分）。",
        "",
        "## 結論",
        f"- 実行日 {data['date']}、commit `{data['commit']}`、モデル（API の報告値）: {', '.join(models) or '不明'}",
        f"- このアカウントの Astra デプロイで、strict の `submit_plan` を tool_choice で指名した Responses API 呼び出しが **{completed}/{n} 件 completed**、契約 schema（`contracts/plan.schema.json` の完全版）の検証を **{valid}/{n} 件** 通った",
        f"- 事前に書いた期待（status・target・destination）との一致: **{matched}/{n}**。期待が ok でない文から実行可能プランが返った件数: **{len(false_exec)}**{'（' + ', '.join(false_exec) + '）' if false_exec else ''}",
        f"- 不一致: {'、'.join(mismatches) if mismatches else 'なし'}。本 Issue ではプロンプトを直さない（作り込みと 1 回の直しは Uc）",
        f"- 所要（リクエスト送信 〜 応答受信）: 中央値 {statistics.median(times):.1f} 秒、最大 {max(times):.1f} 秒（待機上限の暫定値 {data['timeout_s']:.0f} 秒）",
        "- これは文 → JSON の段だけの測定で、文は人が書いた。動画からの通し（Cosmos → Astra）は母艦分。3 種 7 文の結果を品質の受け入れと同一視しない",
        "",
        "## 各文の結果",
        "| id | 種別 | 期待 | 結果 status / target / destination | 一致 | 実行可能 | API status | 契約検証 | 所要 (s) |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        e, p = r["expected"], r["plan"]
        lines.append(
            f"| {r['id']} | {r['kind']} | {e['status']} / {e['target']} / {e['destination']} "
            f"| {p['status']} / {p['target']} / {p['destination']} | {'○' if r['matches_expected'] else '×'} "
            f"| {'yes' if r['executable'] else 'no'} | {r['api_status']} | {'pass' if r['contract_valid'] else 'FAIL: ' + (r['failure'] or '')} | {r['elapsed_s']:.1f} |"
        )
    lines += ["", "## 送った文と生の tool call arguments"]
    for r in rows:
        lines += [
            "",
            f"### {r['id']} {r['kind']}",
            f"- 文: {r['sentence']}",
            f"- response id: `{r['response_id']}`、usage: input {r['usage'].get('input_tokens')} / output {r['usage'].get('output_tokens')} tokens",
            "- arguments:",
            "",
            "```json",
            r["raw_arguments"] or "(none)",
            "```",
        ]
    lines += [
        "",
        "## 条件",
        "- 接続: Azure OpenAI の v1 API `POST {AOAI_ENDPOINT}/openai/v1/responses`、`model` = `AOAI_ASTRA_DEPLOYMENT`。キーとエンドポイントは `.env`（リポジトリ外）",
        "- リクエスト: `instructions`（登録 ID と status の規則）+ `input`（`Sentence: <文>`）、`tools` = strict の `submit_plan` 1 つ、`tool_choice` で `submit_plan` を指名、`parallel_tool_calls: false`、`store: false`。全文は `astra-roundtrip.json` の `request_template`",
        "- 判定: `src/so101_demo/understanding/astra.py` の `classify_response`。completed で `submit_plan` がちょうど 1 回、引数が契約 schema を通ったものだけを plan とし、それ以外は `status: error`（実行不可）",
        "- 期待値は実行前に `src/so101_demo/evidence/s1_diagnosis.py` の `ROUNDTRIP_SENTENCES` に書いた。Astra を採点者にしない",
        "",
        "## 出典",
        "- `docs/handover/understanding-layer-notes.md` §3（strict・tool_choice・parallel_tool_calls・refusal / incomplete の扱い）、§5（Astra 単体評価の文の種類）",
        "- `docs/demo-plan.md`「理解層の設計」",
    ]
    (OUT_DIR / "astra-roundtrip.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (OUT_DIR / "astra-roundtrip.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


RUNS = 3  # per video; the text is not reproducible even with a fixed seed
HUMAN_CHECK = OUT_DIR / "human-check.json"
COMPARISON = OUT_DIR / "prompt-comparison.json"
PROBE = REPO_ROOT / "serving" / "cosmos" / "probe_video.py"


def _plan_dict(p) -> dict:
    return {
        "status": p.status,
        "target": p.target,
        "destination": p.destination,
        "steps": [{k: v for k, v in vars(s).items() if v is not None} for s in p.steps],
        "note": p.note,
    }


def _probe(video) -> dict:
    """The video processor's view of one video (serving env); the error text if it fails."""
    cmd = ["uv", "run", "--project", str(PROBE.parent), "--frozen", "python", str(PROBE)]
    try:
        out = subprocess.run(
            [*cmd, str(video), str(S.COSMOS_SAMPLE_FPS)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=600,
        )
        return json.loads(out.stdout.strip().splitlines()[-1])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError) as e:
        return {"error": repr(e)[:300]}


def _server_model(base_url: str) -> str | None:
    import urllib.request

    try:
        with urllib.request.urlopen(base_url.rstrip("/") + "/v1/models", timeout=5) as r:
            return json.loads(r.read())["data"][0]["id"]
    except (OSError, ValueError, KeyError, IndexError):
        return None


def _cosmos_row(c) -> dict:
    return {
        "sentence": c.sentence,
        "failure": c.failure,
        "finish_reason": c.finish_reason,
        "reasoning": c.reasoning,
        "elapsed_s": round(c.elapsed_s, 2),
        "prompt_tokens": c.usage.get("prompt_tokens"),
        "completion_tokens": c.usage.get("completion_tokens"),
    }


def run_diagnosis(runs: int = RUNS) -> dict:
    model = _server_model(S.COSMOS_BASE_URL)
    if model is None:
        raise SystemExit(f"no Cosmos server at {S.COSMOS_BASE_URL}; start serving/cosmos/serve.sh")
    manifest, videos = load_manifest()
    probes = {v.id: _probe(v.path) for v in videos}
    print("warm-up ...")
    warm = cosmos.describe(videos[0].path)
    rows = []
    for run in range(1, runs + 1):
        for v in videos:
            u = pipeline.understand(v.path)
            a = u.astra
            rows.append(
                {
                    "video": v.id,
                    "run": run,
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
                        "usage": a.usage,
                    },
                    "failed_stage": u.failed_stage,
                    "plan": _plan_dict(u.plan),
                    "executable": u.plan.executable,
                    "matches_gold": (u.plan.status, u.plan.target, u.plan.destination)
                    == (v.gold["status"], v.gold.get("target"), v.gold.get("destination")),
                    "total_s": round(u.elapsed_s, 2),
                }
            )
            r = rows[-1]
            print(
                f"run {run} {v.id}: cosmos {r['cosmos']['elapsed_s']} s "
                f"{r['cosmos']['finish_reason']} -> {u.plan.status} {u.plan.target} "
                f"{u.plan.destination} ({r['total_s']} s) {u.failed_stage or ''}"
            )
    return {
        "date": dt.date.today().isoformat(),
        "commit": _git_head(),
        "cosmos_server_model": model,
        "settings": {
            k: getattr(S, k)
            for k in dir(S)
            if k.isupper() and (k.startswith("COSMOS_") or k.startswith("ASTRA_"))
        },
        "cosmos_request_template": cosmos.build_request(videos[0].path),
        "manifest_version": manifest["version"],
        "videos": [
            {
                "id": v.id,
                "path": str(v.path.relative_to(REPO_ROOT)),
                "sha256": v.sha256,
                "scope": v.scope,
                "out_of_scope_kind": v.out_of_scope_kind,
                "gold": v.gold,
                "processor": probes[v.id],
            }
            for v in videos
        ],
        "warmup": {"video": videos[0].id, **_cosmos_row(warm)},
        "runs": runs,
        "rows": rows,
    }


def _stats(xs: list[float]) -> str:
    if not xs:
        return "未測定（呼んでいない）"
    return f"中央値 {statistics.median(xs):.1f} 秒、最大 {max(xs):.1f} 秒"


def _num(x, fmt: str = ",") -> str:
    """A measured number, or 「—」 when the stage never produced it (a failed or skipped call)."""
    return "—" if x is None else format(x, fmt)


def _count(xs) -> str:
    """「timeout 2、length 1」 from failure names; 「なし」 when empty."""
    c: dict = {}
    for x in xs:
        c[x] = c.get(x, 0) + 1
    return "、".join(f"{k} {v}" for k, v in c.items()) or "なし"


def check_matches(check: dict, sentences: set) -> bool:
    """A hand check covers a run only if it names exactly the sentences it was made on.

    The check is tied to the text, not the video id: a rerun can return other sentences
    (seed fixed or not), and those have not been checked by anyone.
    """
    return set(check.get("sentences", [])) == sentences


def _comparison_lines() -> list[str]:
    """「プロンプトの比較」 from prompt-comparison.json (the A/B that changed the prompt)."""
    if not COMPARISON.is_file():
        return []
    cmp = json.loads(COMPARISON.read_text(encoding="utf-8"))
    lines = [
        "## プロンプトの比較（Cosmos への指示を差し替えた根拠）",
        f"{cmp['date']}、commit `{cmp['commit']}` で、同じサーバー・同じリクエストのまま"
        f"指示文と seed（{', '.join(map(str, cmp['seeds']))}）だけを変えて 3 本を流した"
        "（`prompt-comparison.json`）。最初の診断（7c3b93a の `diagnosis.json`）では s1-03（持ち上げて戻すだけ）で Cosmos が"
        "トレーへの投入を 2 回作った。指示文の「容器に入ったか」の問いが誘ったと見て、"
        "容器に触れない指示文と比べた。ユーザーの判断で、診断は容器に触れない指示文でやり直した"
        "（本文の結果はすべてこちら）。",
        "",
    ]
    for name, text in cmp["prompts"].items():
        lines += [f"- `{name}`: {text}"]
    lines += [
        "",
        "| 指示文 | 動画 | seed | finish_reason | 一文 |",
        "|---|---|---|---|---|",
    ]
    for r in cmp["rows"]:
        sentence = (r["sentence"] or f"(なし: {r['failure']})").replace("|", "\\|")
        lines.append(f"| {r['prompt']} | {r['video']} | {r['seed']} | {r['finish']} | {sentence} |")
    lines += [
        "",
        "同じ seed 1234・同じ指示文でも、最初の診断と比較とで s1-03 の文が違った。"
        "seed を固定しても文の再現は保証されない。",
        "",
    ]
    return lines


def write_diagnosis() -> None:
    """diagnosis.md from diagnosis.json and the hand-written human-check.json."""
    data = json.loads((OUT_DIR / "diagnosis.json").read_text(encoding="utf-8"))
    human = json.loads(HUMAN_CHECK.read_text(encoding="utf-8")) if HUMAN_CHECK.is_file() else {}
    checks = human.get("videos", {})
    rows = data["rows"]
    n = len(rows)
    videos = data["videos"]
    cos_fail = [r["cosmos"]["failure"] for r in rows if r["failed_stage"] == "cosmos"]
    stop = sum(r["cosmos"]["finish_reason"] == "stop" for r in rows)
    length = sum(r["cosmos"]["finish_reason"] == "length" for r in rows)
    astra_rows = [r for r in rows if r["astra"]]
    valid = sum(r["astra"]["contract_valid"] for r in astra_rows)
    ast_fail = [r["astra"]["failure"] for r in astra_rows if not r["astra"]["contract_valid"]]
    scope = {v["id"]: v["scope"] for v in videos}
    false_exec = [
        f"{r['video']} run {r['run']}"
        for r in rows
        if r["executable"] and scope[r["video"]] != "in_scope"
    ]
    s = data["settings"]
    t_cos = [r["cosmos"]["elapsed_s"] for r in rows]
    t_ast = [r["astra"]["elapsed_s"] for r in astra_rows]
    t_tot = [r["total_s"] for r in rows]
    max_completion = max((r["cosmos"]["completion_tokens"] or 0 for r in rows), default=0)
    confirmed = "ユーザー確認済み" if human.get("user_confirmed") else "ユーザー確認待ち"
    same = sum(
        len({r["cosmos"]["sentence"] for r in rows if r["video"] == v["id"]}) == 1 for v in videos
    )

    def gold_line(v):
        g = v["gold"]
        return f"{g['status']} / {g.get('target')} / {g.get('destination')}"

    def check_for(v):
        """The hand check of this video, or None when it was made on other sentences."""
        c = checks.get(v["id"])
        sentences = {r["cosmos"]["sentence"] for r in rows if r["video"] == v["id"]}
        return c if c and check_matches(c, sentences) else None

    def stages(v):
        vr = [r for r in rows if r["video"] == v["id"]]
        c = check_for(v)
        failed = [r["cosmos"]["failure"] for r in vr if r["failed_stage"] == "cosmos"]
        if failed:
            cos = f"未成立（Cosmos が失敗を返した {len(failed)}/{len(vr)}: {_count(failed)}）"
        elif c is None:
            cos = "未照合"
        elif c["verdict"].startswith("不一致"):
            cos = "未成立: " + c["verdict"]
        else:
            cos = "成立"
        called = [r for r in vr if r["astra"]]
        ok = sum(r["astra"]["contract_valid"] for r in called)
        bad = [r["astra"]["failure"] for r in called if not r["astra"]["contract_valid"]]
        if not called:
            ast = "呼んでいない（Cosmos が失敗）"
        elif bad:
            ast = f"未成立（契約検証 {ok}/{len(called)}、失敗: {_count(bad)}）"
        else:
            ast = f"成立（契約検証 {ok}/{len(called)}）"
        if len(called) < len(vr) and called:
            ast += f"、{len(vr) - len(called)} 回は呼んでいない"
        ok_final = all(
            r["matches_gold"] or (v["scope"] != "in_scope" and not r["executable"]) for r in vr
        )
        end = (
            "gold と一致"
            if all(r["matches_gold"] for r in vr)
            else ("実行不可で止まった（gold の status とは別）" if ok_final else "gold と不一致")
        )
        return cos, ast, end

    lines = [
        "# S1 診断: 10 秒動画 3 本で Cosmos 一文 → Astra 契約 JSON が通るか",
        "",
        "`uv run python -m so101_demo.evidence.s1_diagnosis` が生成（Issue #22 母艦分）。"
        "生データは `diagnosis.json`、照合は `human-check.json`（手書き）。",
        "",
        "## 結論",
        f"- 実行日 {data['date']}、commit `{data['commit']}`。"
        f"動画 {len(videos)} 本 × {data['runs']} 回 = {n} 実行（ウォームアップ 1 回は別）",
        f"- Cosmos: {n - len(cos_fail)}/{n} 実行で一文を返した（finish_reason stop {stop}/{n}、"
        f"失敗 {len(cos_fail)}{': ' + _count(cos_fail) if cos_fail else ''}）。同じ動画の {data['runs']} 回で文が"
        f"一字一句同じだった動画: {same}/{len(videos)}"
        "（seed 固定でも再現は保証されない。「プロンプトの比較」）",
        f"- Astra: 呼んだのは {len(astra_rows)}/{n} 実行"
        + ("（残りは Cosmos の失敗で呼んでいない）" if len(astra_rows) < n else "")
        + f"、契約 schema の検証を **{valid}/{len(astra_rows)}** 通った"
        + (f"（失敗: {_count(ast_fail)}）" if ast_fail else ""),
        f"- 対応外の動画から実行可能プランが返った件数: **{len(false_exec)}**"
        + (f"（{', '.join(false_exec)}）" if false_exec else ""),
        "- 段ごとの成否（Cosmos は人の照合、Astra は契約検証、最終は gold との比較）:",
    ]
    for v in videos:
        cos, ast, end = stages(v)
        lines.append(f"  - {v['id']}（{v['scope']}）: Cosmos {cos} / Astra {ast} / 最終 {end}")
    lines += [
        "- **未成立の段: "
        + (
            "、".join(
                f"{v['id']} の {name} 段"
                for v in videos
                for name, st in zip(("Cosmos", "Astra", "最終"), stages(v), strict=True)
                if st.startswith("未成立") or st == "gold と不一致"
            )
            or "なし"
        )
        + "**。詳細は各動画の「照合」",
        f"- 所要（ウォームアップ後）: Cosmos {_stats(t_cos)}、Astra {_stats(t_ast)}、"
        f"動画投入 〜 検証済み最終 JSON {_stats(t_tot)}。"
        f"ウォームアップ 1 回は {data['warmup']['elapsed_s']:.1f} 秒（起動直後の初回なら"
        "約 19 秒。この実行ではサーバーが既に温まっていた場合がある。`serving.md`）",
        f"- 運用値は暫定値のまま固定: Cosmos `max_tokens` {s['COSMOS_MAX_TOKENS']}、"
        f"Cosmos タイムアウト {s['COSMOS_TIMEOUT_S']:.0f} 秒、"
        f"Astra 待機上限 {s['ASTRA_TIMEOUT_S']:.0f} 秒（根拠は「運用値」）",
        "",
        "## 運用値（demo-plan「未決の値」4）",
        "| 値 | 固定値 | 実測 | 根拠 |",
        "|---|---|---|---|",
        f"| Cosmos `max_tokens` | {s['COSMOS_MAX_TOKENS']} | 完了トークン最大 {max_completion}"
        "（推論トレース込み） | "
        + (
            f"最大の {s['COSMOS_MAX_TOKENS'] / max_completion:.1f} 倍の余裕。"
            if max_completion
            else "完了トークンを測れていない（全件失敗）。"
        )
        + "生成は約 115 tokens/s（`serving.md`）なので、暴走しても約 9 秒で `length` で止まり、"
        "`finish_reason` 検査で失敗になる |",
        f"| Cosmos タイムアウト | {s['COSMOS_TIMEOUT_S']:.0f} 秒 | 最大 {_num(max(t_cos, default=None), '.1f')} 秒"
        f"（ウォームアップ除く） | 暴走の上限（プリフィル約 1 秒 + 1024 トークン 約 9 秒）の"
        "約 3 倍。ウォームアップ（約 19 秒）もこの中に収まる |",
        f"| Astra 待機上限 | {s['ASTRA_TIMEOUT_S']:.0f} 秒 | 最大 {_num(max(t_ast, default=None), '.1f')} 秒"
        "（`astra-roundtrip.md` の人手の文では最大 5.7 秒） | 最大の 3 倍以上。Cosmos の上限と"
        "足しても 60 秒で、動画投入 〜 最終 JSON の実測最大の 5 倍以上 |",
        "",
        (
            f"この {len(videos)} 本・{n} 実行では Cosmos の暴走（`length`）は 1 度も出ていない。"
            if not length
            else f"この {len(videos)} 本・{n} 実行で Cosmos の暴走（`length`）が {length} 回出た。"
        )
        + "上限が実際に効くかは、毎回違う動画を使う Uc で見る。",
        "",
    ]
    for v in videos:
        vr = [r for r in rows if r["video"] == v["id"]]
        pr = v["processor"]
        c = check_for(v)
        lines += [
            f"## {v['id']}（{v['scope']}"
            + (f"、{v['out_of_scope_kind']}" if v["out_of_scope_kind"] else "")
            + "）",
            f"- 動画: `{v['path']}`（sha256 `{v['sha256'][:12]}…`）",
            f"- gold: {gold_line(v)}。事実: {v['gold']['facts']}",
        ]
        if "error" in pr:
            lines.append(f"- 前処理: 取得できなかった（{pr['error']}）")
        else:
            lines.append(
                f"- 前処理（モデルの video processor の実測）: 元 {pr['source_frames']} フレーム "
                f"{pr['source_hw'][1]}×{pr['source_hw'][0]} @ {pr['source_fps']:g} fps → "
                f"sample fps {pr['sample_fps']:g} で **{pr['sampled_frames']} フレーム**、"
                f"1 フレーム **{pr['frame_hw'][1]}×{pr['frame_hw'][0]}**。画素予算は全フレーム"
                f"合計 {pr['pixel_budget']['total_max']:,} 画素（下限 "
                f"{pr['pixel_budget']['min']:,}）で、それに合わせて縮小される。"
                f"動画トークン {pr['video_tokens']:,}（サーバーの prompt_tokens は "
                f"{_num(vr[0]['cosmos']['prompt_tokens'])}）"
            )
        sentences = {r["cosmos"]["sentence"] for r in vr}
        lines += [
            "",
            "| run | Cosmos (s) | finish_reason | 完了トークン | Astra (s) | API status | 契約検証 "
            "| 結果 status / target / destination | 実行可能 | gold 一致 | 合計 (s) |",
            "|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for r in vr:
            a, p = r["astra"], r["plan"]
            lines.append(
                f"| {r['run']} | {r['cosmos']['elapsed_s']:.2f} "
                f"| {r['cosmos']['finish_reason'] or '— (' + str(r['cosmos']['failure']) + ')'} "
                f"| {_num(r['cosmos']['completion_tokens'])} | "
                + (
                    f"{a['elapsed_s']:.2f} | {a['api_status'] or '—'} | "
                    + ("pass" if a["contract_valid"] else "FAIL: " + (a["failure"] or ""))
                    if a
                    else "— | — | —"
                )
                + f" | {p['status']} / {p['target']} / {p['destination']} "
                f"| {'yes' if r['executable'] else 'no'} | {'○' if r['matches_gold'] else '×'} "
                f"| {r['total_s']:.2f} |"
            )
        lines += [
            "",
            "Cosmos の一文" + ("（全回同じ）" if len(sentences) == 1 else "（回ごと）") + ":",
            "",
        ]
        for r in vr if len(sentences) > 1 else vr[:1]:
            lines.append(
                f"> {r['cosmos']['sentence'] or '(なし: ' + str(r['cosmos']['failure']) + ')'}"
            )
            lines.append("")
        lines += ["Astra の生 tool call arguments:", ""]
        for r in vr:
            lines += [
                f"- run {r['run']}: `{r['astra']['raw_arguments'] if r['astra'] else '(呼んでいない)'}`"
            ]
        lines += ["", f"照合（gold と映像、{confirmed if c else '未照合'}）:", ""]
        if c:
            lines += [
                f"- 判定: **{c['verdict']}**",
                f"- 対象: {c['target']}",
                f"- 置き先: {c['destination']}",
                f"- 順序: {c['order']}",
                f"- 完了 / 未完了: {c['completion']}",
                f"- 落とした曖昧さ: {c['ambiguity_dropped']}",
                f"- 映像に無い事実: {c['added_facts']}",
            ] + ([f"- 注: {c['note']}"] if c.get("note") else [])
        elif checks.get(v["id"]):
            lines.append(
                "- 未照合（`human-check.json` の照合は別の文に対するもの。今の文は誰も見ていない）"
            )
        else:
            lines.append("- 未照合")
        lines.append("")
    lines += _comparison_lines()
    lines += [
        "## 条件",
        f"- Cosmos: `{data['cosmos_server_model']}`（vLLM 0.12.0、`serving/cosmos/serve.sh`、"
        "起動と VRAM は `serving.md`）。リクエストは `diagnosis.json` の "
        "`cosmos_request_template`（`src/so101_demo/understanding/cosmos.py`）。"
        f"temperature {s['COSMOS_TEMPERATURE']}、top_p {s['COSMOS_TOP_P']}、top_k "
        f"{s['COSMOS_TOP_K']}、seed {s['COSMOS_SEED']}、sample fps {s['COSMOS_SAMPLE_FPS']:g}",
        "- Astra: `astra-roundtrip.md` と同じ呼び方（strict の `submit_plan`、tool_choice で指名）。"
        "判定は `astra.classify_response`、契約は `contracts/plan.schema.json`",
        "- 所要: Cosmos / Astra は各リクエストの送信 〜 応答受信。合計は `pipeline.understand` の"
        "動画投入 〜 検証済み最終 JSON",
        "- 照合: `human-check.json` に手で書いた。Astra を採点者にしない。"
        "照合は 1 fps のフレーム一覧と manifest の gold の事実で行った",
        "",
        "## 出典",
        "- `contracts/fixtures/s1/videos.json`（動画、gold）",
        "- `docs/handover/cosmos-measurements.md` §3、§4（所要、暴走、捏造の既知の癖）",
        "- `docs/demo-plan.md`「理解層の設計」「未決の値」",
    ]
    (OUT_DIR / "diagnosis.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check-manifest", action="store_true", help="check videos.json and hashes")
    ap.add_argument("--astra-roundtrip", action="store_true", help="real Astra API round trip")
    ap.add_argument("--render", action="store_true", help="rebuild diagnosis.md from the json")
    args = ap.parse_args(argv)
    if args.check_manifest:
        problems = check_manifest()
        for p in problems:
            print(f"manifest: {p}", file=sys.stderr)
        if problems:
            return 1
        print("manifest: OK (3 videos, hashes match)")
    if args.astra_roundtrip:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        write_roundtrip(run_roundtrip())
        print(OUT_DIR / "astra-roundtrip.md")
    if args.check_manifest or args.astra_roundtrip:
        return 0
    if not args.render:
        if check_manifest():
            print("manifest is not valid; run --check-manifest", file=sys.stderr)
            return 1
        # A run that dies half-way must not leave the previous run's report looking current.
        (OUT_DIR / "diagnosis.md").unlink(missing_ok=True)
        data = run_diagnosis()
        (OUT_DIR / "diagnosis.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    write_diagnosis()
    print(OUT_DIR / "diagnosis.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
