"""Fault injection of the Astra stage against a local fake server (Issue #23).

    uv run python -m so101_demo.evidence.astra_faults

Every case goes through the real chain: `pipeline.understand` with a `RunGate`, the real
`transport.post_json`, and a local HTTP server that plays both Cosmos (always one good
sentence) and Astra (the injected fault). Nothing calls the real API. The Astra deadline
is shortened to `ASTRA_TIMEOUT_S` below for the run, so the deadline cases stay short.

For each case the record keeps the injected fault, Astra's response status, the final
plan status, the stage and the failure, Astra's elapsed time, whether the plan is
executable, and whether `approve_from_review` accepts it after `RunGate.claim` (it must
not, except for the control case). The report is docs/evidence/astra-faults/faults.md,
the rows are faults.json next to it. The command exits 1 when any fault case ends
executable or approvable, a deadline case overruns, or the control case fails.
"""

from __future__ import annotations

import http.server
import json
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..execution.runner import approve_from_review
from ..plan.paths import REPO_ROOT
from ..understanding import pipeline
from ..understanding import settings as S
from ..understanding.runs import RunGate, StaleRun

OUT_DIR = REPO_ROOT / "docs" / "evidence" / "astra-faults"
VIDEO = REPO_ROOT / "docs" / "evidence" / "s1" / "videos" / "s1-01-red-to-box_a.mp4"
SENTENCE = "A hand puts the red cube into the tray on the left."
# Shortened deadlines for this run only (the operational values are in settings.py).
ASTRA_TIMEOUT_S = 1.0
RUN_TIMEOUT_S = 0.6  # gate deadline of the run-expiry case only
TIMING_MARGIN_S = 0.5  # Issue #23: deadline cases end within ASTRA_TIMEOUT_S + 0.5 s

OK_ARGS = {
    "status": "ok",
    "target": "red_cube",
    "destination": "box_a",
    "steps": [
        {"action": "pick", "object": "red_cube"},
        {"action": "place", "destination": "box_a"},
    ],
    "note": "赤いキューブを左の箱へ",
}


def _call(args: Any, name: str = "submit_plan", call_id: str = "c1") -> dict[str, Any]:
    raw = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
    return {"type": "function_call", "name": name, "arguments": raw, "call_id": call_id}


def _response(*output: dict[str, Any], status: str = "completed") -> dict:
    return {"id": "resp_fake", "model": "fake-astra", "status": status, "output": list(output)}


def _message(*parts: dict[str, Any]) -> dict[str, Any]:
    return {"type": "message", "role": "assistant", "content": list(parts)}


COSMOS_BODY = {
    "choices": [{"finish_reason": "stop", "message": {"content": SENTENCE}}],
    "usage": {"completion_tokens": 12},
}


# --- the fake server ------------------------------------------------------------------

Handler = Callable[[http.server.BaseHTTPRequestHandler, "Case", "FakeServer"], None]


def _send_json(h: http.server.BaseHTTPRequestHandler, status: int, body: dict) -> None:
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    h.send_response(status)
    h.send_header("Content-Type", "application/json")
    h.send_header("Content-Length", str(len(raw)))
    h.end_headers()
    h.wfile.write(raw)


def reply(body: dict, status: int = 200) -> Handler:
    return lambda h, case, srv: _send_json(h, status, body)


def drop(h, case, srv) -> None:
    """Close the connection without a status line."""
    h.close_connection = True
    h.connection.shutdown(2)


def trickle(h, case, srv) -> None:
    """A complete-looking response whose body arrives one byte per 0.1 s."""
    raw = json.dumps(_response(_call(OK_ARGS))).encode("utf-8")
    h.send_response(200)
    h.send_header("Content-Type", "application/json")
    h.send_header("Content-Length", str(len(raw)))
    h.end_headers()
    for byte in raw:
        if srv.stop.wait(0.1):
            return
        try:
            h.wfile.write(bytes([byte]))
            h.wfile.flush()
        except OSError:
            return  # the client gave up at its deadline


def late(delay_s: float) -> Handler:
    """A good response, sent `delay_s` after the request arrived."""

    def handle(h, case, srv) -> None:
        if srv.stop.wait(delay_s):
            return
        try:
            _send_json(h, 200, _response(_call(OK_ARGS)))
        except OSError:
            pass  # the client already closed at its deadline

    return handle


def held(h, case, srv) -> None:
    """A good response, held until the case's action (cancel / replace / expiry) is done."""
    case.request_seen.set()
    case.release.wait(10.0)
    _send_json(h, 200, _response(_call(OK_ARGS)))


class FakeServer:
    """`/cosmos/v1/chat/completions` answers the sentence; `/<case>/openai/v1/responses`
    runs that case's handler."""

    def __init__(self, cases: dict[str, Case]) -> None:
        self.stop = threading.Event()
        srv = self

        class H(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self) -> None:
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                if self.path == "/cosmos/v1/chat/completions":
                    _send_json(self, 200, COSMOS_BODY)
                    return
                name = self.path.split("/")[1]
                case = cases.get(name)
                if case is None or self.path != f"/{name}/openai/v1/responses":
                    _send_json(self, 404, {"error": f"no route {self.path}"})
                    return
                case.handler(self, case, srv)

            def log_message(self, *a: Any) -> None:
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.httpd.daemon_threads = True
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def __enter__(self) -> FakeServer:
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *a: Any) -> None:
        self.stop.set()
        self.httpd.shutdown()
        self.httpd.server_close()


# --- cases ----------------------------------------------------------------------------


@dataclass
class Case:
    id: str
    fault: str  # what is injected, in Japanese, for the report
    handler: Handler
    action: str | None = None  # None, "cancel", "replace", "expire"
    deadline: bool = False  # the elapsed time is checked against ASTRA_TIMEOUT_S + margin
    control: bool = False  # the one case that must end executable
    request_seen: threading.Event | None = None
    release: threading.Event | None = None

    def __post_init__(self) -> None:
        self.request_seen = threading.Event()
        self.release = threading.Event()


def cases() -> list[Case]:
    bad_contract = {**OK_ARGS, "steps": [OK_ARGS["steps"][0] | {"object": "blue_cube"}]}
    bad_contract["steps"].append(OK_ARGS["steps"][1])
    return [
        Case(
            "control",
            "対照: 正常な応答（submit_plan 1 回、契約どおり）",
            reply(_response(_call(OK_ARGS))),
            control=True,
        ),
        Case(
            "refusal",
            "refusal（message に refusal）",
            reply(_response(_message({"type": "refusal", "refusal": "I can't help with that."}))),
        ),
        Case(
            "incomplete",
            "incomplete（max_output_tokens で打ち切り）",
            reply(
                _response(status="incomplete")
                | {"incomplete_details": {"reason": "max_output_tokens"}}
            ),
        ),
        Case(
            "no-tool-call",
            "tool call 不在（文章だけ返す）",
            reply(_response(_message({"type": "output_text", "text": "red cube to box_a"}))),
        ),
        Case(
            "two-tool-calls",
            "tool call 複数（submit_plan 2 回）",
            reply(_response(_call(OK_ARGS), _call(OK_ARGS, call_id="c2"))),
        ),
        Case(
            "arguments-not-json",
            "arguments が JSON でない",
            reply(_response(_call('{"status": "ok", "target": "red_cube"'))),
        ),
        Case(
            "tool-schema",
            "tool schema 不正（status に enum 外の値、steps 欠落）",
            reply(
                _response(
                    _call({k: v for k, v in OK_ARGS.items() if k != "steps"} | {"status": "maybe"})
                )
            ),
        ),
        Case(
            "contract",
            "契約違反（target は red_cube なのに steps は blue_cube を pick）",
            reply(_response(_call(bad_contract))),
        ),
        Case(
            "http-429",
            "HTTP 429（レート制限）",
            reply({"error": {"code": "429", "message": "Rate limit"}}, 429),
        ),
        Case(
            "http-500",
            "HTTP 500（サーバー障害）",
            reply({"error": {"code": "500", "message": "Internal error"}}, 500),
        ),
        Case("connection-drop", "接続断（応答前に切断）", drop),
        Case(
            "trickle",
            "本文が 0.1 秒に 1 バイトずつ届き期限を超える",
            trickle,
            deadline=True,
        ),
        Case(
            "late",
            f"期限後に届く応答（{ASTRA_TIMEOUT_S + 1.0:g} 秒後に正常応答）",
            late(ASTRA_TIMEOUT_S + 1.0),
            deadline=True,
        ),
        Case("cancelled", "取り消し後に届く応答（正常応答）", held, action="cancel"),
        Case("replaced", "次の依頼で置き換えた後に届く応答（正常応答）", held, action="replace"),
        Case(
            "run-expired",
            f"run の期限（{RUN_TIMEOUT_S:g} 秒）切れの後に届く応答（正常応答）",
            held,
            action="expire",
        ),
    ]


# --- running --------------------------------------------------------------------------


def _layer(failed_stage: str | None, failure: str | None) -> str:
    """Which part of the Astra stage failed, from the recorded failure."""
    if failed_stage == "run":
        return "run ID"
    if failed_stage is None:
        return "-"
    if failed_stage == "cosmos":
        return "Cosmos"
    f = failure or ""
    if f.startswith(("timeout", "transport")):
        return "通信"
    if f.startswith(("api error", "incomplete", "status=", "refusal", "output", "message")):
        return "応答"
    if f.startswith(("no function_call", "function_call", "arguments")) or "function_calls" in f:
        return "tool call"
    if f.startswith("contract schema"):
        return "契約検証"
    return "?"


def run_case(case: Case, srv: FakeServer) -> dict[str, Any]:
    env = {
        "AOAI_ENDPOINT": f"{srv.base}/{case.id}",
        "AOAI_API_KEY": "fake-key",
        "AOAI_ASTRA_DEPLOYMENT": "fake-astra",
    }
    gate = RunGate(timeout_s=RUN_TIMEOUT_S if case.action == "expire" else None)
    run_id = gate.start()
    out: dict[str, Any] = {}

    def work() -> None:
        out["u"] = pipeline.understand(
            VIDEO, gate=gate, run_id=run_id, env=env, base_url=f"{srv.base}/cosmos"
        )

    worker = threading.Thread(target=work, name=f"case-{case.id}")
    worker.start()
    replaced_by = None
    if case.action is not None:
        if not case.request_seen.wait(10.0):
            raise RuntimeError(f"{case.id}: the Astra request never arrived")
        if case.action == "cancel":
            gate.cancel(run_id)
        elif case.action == "replace":
            replaced_by = gate.start()
        elif case.action == "expire":
            time.sleep(RUN_TIMEOUT_S + 0.1)
        case.release.set()
    worker.join()
    u = out["u"]
    a = u.astra
    try:
        plan, rid = gate.claim(u.run_id, u.plan)
        approve_from_review(plan, rid)
        approvable = True
    except (StaleRun, ValueError):
        approvable = False
    failure = u.stale if u.failed_stage == "run" else (a.failure if a else u.cosmos.failure)
    return {
        "id": case.id,
        "fault": case.fault,
        "control": case.control,
        "deadline": case.deadline,
        "run_id": run_id,
        "result_run_id": u.run_id,
        "replaced_by": replaced_by,
        "astra_api_status": a.api_status if a else None,
        "astra_plan_status": a.plan.status if a else None,
        "status": u.plan.status,
        "failed_stage": u.failed_stage,
        "layer": _layer(u.failed_stage, failure),
        "failure": failure,
        "astra_elapsed_s": round(a.elapsed_s, 3) if a else None,
        "executable": u.plan.executable,
        "approvable": approvable,
    }


# Why each fault case must fail: (stage from `_layer`, prefix of the recorded failure).
# A case that fails for another reason (a 404 of the fake server, a timeout where a
# run-ID drop was meant) is not evidence for what it was built to test.
EXPECTED = {
    "refusal": ("応答", "refusal"),
    "incomplete": ("応答", "incomplete"),
    "no-tool-call": ("tool call", "no function_call"),
    "two-tool-calls": ("tool call", "2 function_calls"),
    "arguments-not-json": ("tool call", "arguments are not"),
    "tool-schema": ("契約検証", "contract schema"),
    "contract": ("契約検証", "contract schema"),
    "http-429": ("通信", "transport: HTTP 429"),
    "http-500": ("通信", "transport: HTTP 500"),
    "connection-drop": ("通信", "transport: connection lost"),
    "trickle": ("通信", "timeout"),
    "late": ("通信", "timeout"),
    "cancelled": ("run ID", "cancelled"),
    "replaced": ("run ID", "superseded"),
    "run-expired": ("run ID", "expired"),
}


def check(row: dict[str, Any]) -> list[str]:
    """What is wrong with one row; empty when the case behaved as required."""
    problems = []
    if row["result_run_id"] != row["run_id"]:
        problems.append("result carries another run ID")
    if row["control"]:
        if not (row["executable"] and row["approvable"]):
            problems.append("control case is not executable and approvable")
        return problems
    if row["status"] == "ok" or row["executable"]:
        problems.append("fault case ended executable")
    if row["approvable"]:
        problems.append("fault case was approved")
    if not row["failure"]:
        problems.append("no failure recorded")
    layer, prefix = EXPECTED.get(row["id"], ("?", "?"))
    if row["layer"] != layer or not (row["failure"] or "").startswith(prefix):
        problems.append(
            f"failed as {row['layer']} / {row['failure']!r}, expected {layer} / {prefix!r}"
        )
    if row["deadline"] and (
        row["astra_elapsed_s"] is None or row["astra_elapsed_s"] > ASTRA_TIMEOUT_S + TIMING_MARGIN_S
    ):
        problems.append(f"deadline case took {row['astra_elapsed_s']} s")
    return problems


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()


def _git_head() -> str:
    """The last commit that changed the code (so committing the report itself does not
    change it), marked when the working tree has uncommitted changes to the code."""
    try:
        head = _git("log", "-1", "--format=%h", "--", "src", "contracts")
        dirty = _git("status", "--porcelain", "--", "src", "contracts")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return f"{head} + uncommitted changes" if dirty else head


def run_all() -> dict[str, Any]:
    all_cases = cases()
    saved = S.ASTRA_TIMEOUT_S
    S.ASTRA_TIMEOUT_S = ASTRA_TIMEOUT_S
    try:
        with FakeServer({c.id: c for c in all_cases}) as srv:
            rows = [run_case(c, srv) for c in all_cases]
    finally:
        S.ASTRA_TIMEOUT_S = saved
    for row in rows:
        row["problems"] = check(row)
    return {
        "commit": _git_head(),
        "date": time.strftime("%Y-%m-%d"),
        "astra_timeout_s": ASTRA_TIMEOUT_S,
        "run_timeout_s_expiry_case": RUN_TIMEOUT_S,
        "timing_margin_s": TIMING_MARGIN_S,
        "rows": rows,
        "passed": all(not r["problems"] for r in rows),
    }


def _yn(b: bool) -> str:
    return "はい" if b else "いいえ"


def report(result: dict[str, Any]) -> str:
    rows = result["rows"]
    faults = [r for r in rows if not r["control"]]
    bad = [r for r in rows if r["problems"]]
    limit = result["astra_timeout_s"] + result["timing_margin_s"]
    deadline_rows = [r for r in faults if r["deadline"]]
    deadline_times = [r["astra_elapsed_s"] for r in deadline_rows]
    deadline_ok = bool(deadline_rows) and all(t is not None and t <= limit for t in deadline_times)
    deadline_max = max((t for t in deadline_times if t is not None), default=None)
    late_rows = [r for r in faults if EXPECTED.get(r["id"], ("",))[0] == "run ID"]
    lines = [
        "# Astra 段の異常系 注入試験（Issue #23）",
        "",
        "## 結論",
        "",
        (
            f"- 注入した異常 {len(faults)} 件のすべてで最終プランは実行可能にならず、"
            "承認（`RunGate.claim` → `approve_from_review`）も通らなかった。"
            if not any(r["executable"] or r["approvable"] for r in faults)
            else "- **実行可能または承認できた異常ケースがある**（下表の「問題」列）。"
        ),
        f"- 期限系（少しずつ届く本文、期限後の応答）の Astra 所要は最大 {deadline_max} 秒で、"
        f"`ASTRA_TIMEOUT_S`（試験値 {result['astra_timeout_s']:g} 秒）+ 0.5 秒 = "
        f"{limit:g} 秒"
        + ("以内。" if deadline_ok else "を**超えた、または所要が記録されていない**。"),
        (
            "- 取り消し・置き換え・run 期限切れの後に届いた正常応答は、Astra 段では `ok` でも "
            "run ID で破棄され `error` になった（段 = run ID）。"
            if all(not r["problems"] for r in late_rows) and late_rows
            else "- **run ID で破棄されなかった遅延応答がある**（下表の「問題」列）。"
        ),
        (
            "- 対照の正常応答は実行可能で承認できた（試験装置が常に失敗を返しているのではない"
            "ことの確認）。"
            if all(not r["problems"] for r in rows if r["control"])
            else "- **対照の正常応答が実行可能・承認可能にならなかった**。"
        ),
        f"- 判定: {'合格' if result['passed'] else '不合格'}（問題のある行 {len(bad)} 件）。",
        "",
        "## 条件",
        "",
        f"- 生成 commit: `{result['commit']}`（{result['date']}）",
        "- コマンド: `uv run python -m so101_demo.evidence.astra_faults`",
        "- 経路: `pipeline.understand`（`RunGate` 付き）→ 実際の `transport.post_json` → "
        "ローカルの偽サーバー（Cosmos は固定の一文、Astra は注入する異常）。実 API は呼ばない",
        f"- `ASTRA_TIMEOUT_S` は試験中だけ {result['astra_timeout_s']:g} 秒に差し替え"
        "（運用値は `understanding/settings.py`）。run 期限切れのケースだけ "
        f"`RunGate(timeout_s={result['run_timeout_s_expiry_case']:g})`",
        "- リトライはしない（demo-plan: リトライ最適化は作らない）",
        "",
        "## 結果",
        "",
        "| # | 注入した異常 | Astra 応答 status | Astra 段の plan | 最終 status | 段 | failure"
        " | Astra 所要 (s) | 実行可能 | 承認 | 問題 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for i, r in enumerate(rows, 1):
        failure = (r["failure"] or "-").replace("|", "\\|").replace("\n", " ")
        if len(failure) > 90:
            failure = failure[:89] + "…"
        lines.append(
            f"| {i} | {r['fault']} | {r['astra_api_status'] or '-'} | "
            f"{r['astra_plan_status'] or '-'} | {r['status']} | {r['layer']} | "
            f"`{failure}` | {r['astra_elapsed_s']} | {_yn(r['executable'])} | "
            f"{_yn(r['approvable'])} | {'; '.join(r['problems']) or '-'} |"
        )
    lines += [
        "",
        "列の意味: 「Astra 応答 status」は Responses API の `status`（HTTP / 通信の失敗では無し）。"
        "「Astra 段の plan」は `astra.structure` が返した plan の status、「最終 status」は "
        "`RunGate.settle` を通った後の status。「段」は failure から読んだ失敗箇所"
        "（通信 / 応答 / tool call / 契約検証 / run ID）。「承認」は `RunGate.claim` と "
        "`approve_from_review` が通ったか。",
        "",
        "## 出典",
        "",
        "- `docs/demo-plan.md` 第 2 段の表「Astra 構造化 / 異常系」、リスク R1（遅延応答は run ID で失効）",
        "- 実装: `src/so101_demo/understanding/runs.py`、`src/so101_demo/understanding/pipeline.py`、"
        "`src/so101_demo/evidence/astra_faults.py`",
        "- 行データ: `docs/evidence/astra-faults/faults.json`",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    result = run_all()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "faults.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (OUT_DIR / "faults.md").write_text(report(result), encoding="utf-8")
    for r in result["rows"]:
        mark = "ok " if not r["problems"] else "NG "
        print(f"{mark}{r['id']:<20} {r['status']:<6} {r['layer']:<9} {r['failure']}")
    print(
        f"wrote {OUT_DIR.relative_to(REPO_ROOT)}/faults.md: {'pass' if result['passed'] else 'FAIL'}"
    )
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
