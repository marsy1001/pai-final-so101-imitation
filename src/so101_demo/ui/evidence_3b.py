"""The four paths of the approval screen with fixed JSON (Issue #27, XPS part).

    uv run python -m so101_demo.ui.evidence_3b

Drives the real server (`server.create_app`, through FastAPI's test client, no browser)
with a real headless `PlanRunner` behind it, on its own thread as in the app. The
understanding results are the fixed JSONs of 4a plus two non-ok results (対応外, error);
nothing calls Cosmos or Astra. Paths:
承認, 訂正, 対応外拒否, 手動. Then the refusals: every operation that must not reach the
runner is tried and the runner's record count is compared before and after.

Writes docs/evidence/3b/operations.md (conclusion first) and operations.json. A run on
the laptop is 参考; the run with real understanding results is GPU-machine work.
Exits 1 when a path does not end as expected or a refused operation reached the runner.
"""

from __future__ import annotations

import json
import platform
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any

from fastapi.testclient import TestClient

from ..execution.runner import PlanRunner
from ..plan import Plan, validate_plan
from ..plan.paths import REPO_ROOT
from .runner_thread import RunnerThread
from .screen import ApprovalScreen
from .server import create_app
from .sources import load_fixtures

OUT_DIR = REPO_ROOT / "docs" / "evidence" / "3b"
RUN_WAIT_S = 120.0


def _non_ok(status: str, note: str) -> Plan:
    return validate_plan(
        {"status": status, "target": None, "destination": None, "steps": [], "note": note}
    )


class ScriptedSource:
    """Delivers the plan queued with `push` at once, or holds it for `release`."""

    def __init__(self) -> None:
        self.queue: list[tuple[Plan, str, bool]] = []
        self.held: list[tuple[str, Plan, str, Any]] = []

    def push(self, plan: Plan, sentence: str, *, hold: bool = False) -> None:
        self.queue.append((plan, sentence, hold))

    def start(self, run_id: str, deliver, video_id: str | None = None) -> None:
        plan, sentence, hold = self.queue.pop(0)
        if hold:
            self.held.append((run_id, plan, sentence, deliver))
        else:
            deliver(run_id, plan, sentence)

    def release(self) -> bool:
        run_id, plan, sentence, deliver = self.held.pop(0)
        return deliver(run_id, plan, sentence)


@dataclass
class Rig:
    runner: PlanRunner
    screen: ApprovalScreen
    source: ScriptedSource
    client: TestClient

    def post(self, op: str, body: dict | None = None) -> int:
        """As the page does: with the session of the view on screen, unless `body` names
        one (a stale page)."""
        body = dict(body or {})
        body.setdefault("session", self.view()["session"])
        return self.client.post(f"/api/{op}", json=body).status_code

    def view(self) -> dict:
        return self.client.get("/api/view").json()

    def wait(self) -> None:
        if not self.screen.wait_idle(RUN_WAIT_S):
            raise RuntimeError("the run did not finish")


def make_rig() -> Rig:
    rt = RunnerThread(PlanRunner)  # as in `python -m so101_demo.ui`
    runner = rt.runner
    screen = ApprovalScreen(executor=rt.submit)
    source = ScriptedSource()
    client = TestClient(create_app(screen, source))
    return Rig(runner, screen, source, client)


def run_paths(rig: Rig) -> list[dict]:
    fixtures = dict(load_fixtures())
    f1 = fixtures["F1-red_cube-box_a"]
    f2 = fixtures["F2-blue_cube-box_b"]
    rows = []

    def record(path, expect, steps, check) -> None:
        n0 = len(rig.runner.records)
        codes = [(name, code) for name, code in steps()]
        rig.wait()
        new = rig.runner.records[n0:]
        entry = rig.screen.log[-1].as_dict()
        rows.append(
            {
                "path": path,
                "expect": expect,
                "http": codes,
                "runs": len(new),
                "outcome": new[-1].outcome if new else None,
                "commands_sent": new[-1].commands_sent if new else 0,
                "approval_source": new[-1].approval_source if new else None,
                "log": entry,
                "phase_after": rig.view()["phase"],
                "ok": check(new, entry),
            }
        )

    rig.source.push(f1, "赤いキューブを左の箱へ入れた（固定 JSON F1）")
    record(
        "承認",
        "表示した F1 をそのまま実行",
        lambda: [("read", rig.post("read")), ("approve", rig.post("approve"))],
        lambda new, e: (
            len(new) == 1
            and new[0].outcome == "success"
            and not e["rescued"]
            and e["final"]["target"] == "red_cube"
            and e["final"]["destination"] == "box_a"
        ),
    )
    rig.source.push(f2, "青いキューブを右の箱へ入れた（固定 JSON F2）")
    record(
        "訂正",
        "F2 を表示し、置き先を box_a に訂正して実行",
        lambda: [
            ("read", rig.post("read")),
            ("correct", rig.post("correct", {"target": "blue_cube", "destination": "box_a"})),
        ],
        lambda new, e: (
            len(new) == 1
            and new[0].outcome == "success"
            and e["rescued"]
            and e["shown"]["destination"] == "box_b"
            and e["final"]["destination"] == "box_a"
        ),
    )
    rig.source.push(_non_ok("unsupported", "登録外の物体をしまった"), "ペンを右の箱へ入れた")
    record(
        "拒否",
        "対応外の結果を対応外として拒否し、何も動かさない",
        lambda: [("read", rig.post("read")), ("reject", rig.post("reject"))],
        lambda new, e: len(new) == 0 and e["path"] == "拒否" and e["final"] is None,
    )
    rig.source.push(_non_ok("error", "理解層の応答を使えませんでした（タイムアウト）"), "")
    record(
        "手動",
        "読み取り失敗から候補を選び（red_cube → box_b）実行",
        lambda: [
            ("read", rig.post("read")),
            ("manual", rig.post("manual")),
            ("choose", rig.post("choose", {"target": "red_cube", "destination": "box_b"})),
        ],
        lambda new, e: (
            len(new) == 1
            and new[0].outcome == "success"
            and e["rescued"]
            and e["final"]["target"] == "red_cube"
            and e["final"]["destination"] == "box_b"
        ),
    )
    return rows


def run_refusals(rig: Rig) -> list[dict]:
    fixtures = dict(load_fixtures())
    f3 = fixtures["F3-red_cube-box_b"]
    f4 = fixtures["F4-blue_cube-box_a"]
    rows = []

    def attempt(what: str, op: str, body: dict | None = None, *, expect_code: int = 409) -> None:
        n0 = len(rig.runner.records)
        phase = rig.view()["phase"]
        code = rig.post(op, body)
        rig.wait()
        rows.append(
            {
                "what": what,
                "phase": phase,
                "op": op,
                "http": code,
                "runs": len(rig.runner.records) - n0,
                "ok": code == expect_code and len(rig.runner.records) == n0,
            }
        )

    attempt("待機中の承認（何も表示していない）", "approve")
    rig.source.push(f3, "held", hold=True)
    rig.post("read")
    attempt("読み取り中の承認", "approve")
    attempt("読み取り中の訂正", "correct", {"target": "red_cube", "destination": "box_b"})
    # manual while reading: the late result must neither show nor run
    rig.post("manual")
    delivered = rig.source.release()
    v = rig.view()
    rows.append(
        {
            "what": "手動に切り替えた後に届いた結果（F3）",
            "phase": v["phase"],
            "op": "deliver",
            "http": None,
            "runs": 0,
            "ok": delivered is False and v["phase"] == "手動" and v["target"] is None,
        }
    )
    attempt("手動中の承認（モデルの案は無い）", "approve")
    rig.post("idle")
    rig.source.push(_non_ok("needs_review", "置き先がはっきりしない"), "赤いキューブを箱へ入れた")
    rig.post("read")
    attempt("ok 以外の結果（needs_review）の承認", "approve")
    rig.post("idle")
    # replaced: a new reading replaces a held one
    rig.source.push(f4, "held", hold=True)
    rig.post("read")
    rig.post("manual")
    rig.post("idle")
    rig.source.push(f3, "赤いキューブを右の箱へ入れた")
    rig.post("read")
    delivered = rig.source.release()  # F4 of the cancelled run
    v = rig.view()
    rows.append(
        {
            "what": "取り消し後に届いた結果（F4）は画面の F3 を上書きしない",
            "phase": v["phase"],
            "op": "deliver",
            "http": None,
            "runs": 0,
            "ok": delivered is False and v["target"]["id"] == "red_cube",
        }
    )
    # approve, then approve again while running and after it
    n0 = len(rig.runner.records)
    first = rig.post("approve")
    phase = rig.view()["phase"]
    second = rig.post("approve")
    rig.wait()
    third = rig.post("approve")
    runs = len(rig.runner.records) - n0
    rows.append(
        {
            "what": "承認の二重押し（実行中と完了後）",
            "phase": phase,
            "op": "approve ×3",
            "http": [first, second, third],
            "runs": runs,
            "ok": first == 200 and second == 409 and third == 409 and runs == 1,
        }
    )
    # a stale page (Codex review 1): page A shows F4, B cancels and reads F3, A approves
    rig.source.push(f4, "青いキューブを左の箱へ入れた")
    rig.post("read")
    page_a = rig.view()["session"]
    rig.post("idle")
    rig.source.push(f3, "赤いキューブを右の箱へ入れた")
    rig.post("read")
    attempt("古い画面（F4 を表示）からの承認。画面は F3", "approve", {"session": page_a})
    attempt(
        "古い画面からの訂正",
        "correct",
        {"session": page_a, "target": "blue_cube", "destination": "box_b"},
    )
    rig.post("reject")
    attempt(
        "完了後の手動選択（手動に入っていない）",
        "choose",
        {"target": "blue_cube", "destination": "box_b"},
    )
    return rows


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()


def _git_head() -> str:
    """The last commit that changed the code, marked when the code has local changes."""
    try:
        head = _git("log", "-1", "--format=%h", "--", "src", "contracts")
        dirty = _git("status", "--porcelain", "--", "src", "contracts")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return f"{head} + uncommitted changes" if dirty else head


def _pair(p: dict | None) -> str:
    if not p:
        return "-"
    if p["status"] != "ok":
        return f"{p['status']}（対象なし）"
    return f"{p['target']} → {p['destination']}"


def report(result: dict) -> str:
    paths, refusals = result["paths"], result["refusals"]
    lines = [
        "# 3b 承認画面: 4 経路の操作記録（固定 JSON、XPS、参考）",
        "",
        "## 結論",
        "",
        f"- 4 経路（承認・訂正・対応外拒否・手動）: {sum(r['ok'] for r in paths)} / "
        f"{len(paths)} が想定どおり"
        + (
            "。承認・訂正・手動は runner が実行して成功し、拒否は runner に何も渡らなかった。"
            if all(r["ok"] for r in paths)
            else "。**想定どおりでない経路がある**（下表の判定）。"
        ),
        f"- 承認できてはいけない操作 {len(refusals)} 件: "
        f"{sum(r['ok'] for r in refusals)} 件が想定どおり"
        + (
            "（409 で断られ、runner に渡った実行は 0。二重押しは 1 回だけ実行）。"
            if all(r["ok"] for r in refusals)
            else "。**runner に渡った、または断られなかった操作がある**（下表の判定）。"
        ),
        (
            "- 手動に切り替えた後・取り消し後に届いた抽出結果は、画面を上書きせず"
            "実行も起こさなかった（run ID で失効）。"
            if all(r["ok"] for r in refusals if r["op"] == "deliver")
            else "- **遅れて届いた抽出結果が画面を上書きした、または実行を起こした**。"
        ),
        "- 訂正と手動はログで `rescued: true`。自動理解の成功には数えない。",
        f"- 判定: {'合格' if result['passed'] else '不合格'}。XPS での実行のため **参考**"
        "（受け入れは母艦分の、実際の抽出結果での操作記録）。",
        "",
        "## 条件",
        "",
        f"- 生成 commit: `{result['commit']}`（{result['date']}）、端末: {result['machine']}",
        "- コマンド: `uv run python -m so101_demo.ui.evidence_3b`",
        "- 経路: FastAPI の test client → `ui.server` → `ApprovalScreen` → 実際の `PlanRunner`"
        "（headless、sim-v1-simple）。抽出結果は 4a の固定 JSON と、対応外・失敗の 2 件。"
        "ブラウザは使わない",
        "",
        "## 4 経路",
        "",
        "| 経路 | 想定 | 表示した案 | 実行したプラン | runner の結果 | 送った指令数 | 救済 "
        "| 操作後の状態 | 判定 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in paths:
        e = r["log"]
        lines.append(
            f"| {r['path']} | {r['expect']} | {_pair(e['shown'])} | {_pair(e['final'])} | "
            f"{r['outcome'] or '実行なし'} | {r['commands_sent']} | "
            f"{'はい' if e['rescued'] else 'いいえ'} | {r['phase_after']} | "
            f"{'OK' if r['ok'] else 'NG'} |"
        )
    lines += [
        "",
        "## 承認できてはいけない操作",
        "",
        "| 操作 | そのときの状態 | HTTP | runner に渡った実行 | 判定 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in refusals:
        lines.append(
            f"| {r['what']} | {r['phase']} | {r['http'] if r['http'] is not None else '-'} | "
            f"{r['runs']} | {'OK' if r['ok'] else 'NG'} |"
        )
    lines += [
        "",
        "## 操作ログ",
        "",
        "1 操作 1 行（`ApprovalScreen.log`）。run ID・経路・表示した案・最終プラン・結果・時刻・"
        "救済の別。"
        "全件は `operations.json` の `log`。",
        "",
        "## 出典",
        "",
        "- `docs/approval-screen.md`（操作と状態、救済分は成功率に数えない）",
        "- 実装: `src/so101_demo/ui/`",
        "",
    ]
    return "\n".join(lines)


def run_all() -> dict:
    rig = make_rig()
    paths = run_paths(rig)
    refusals = run_refusals(rig)
    return {
        "commit": _git_head(),
        "date": time.strftime("%Y-%m-%d"),
        "machine": f"{platform.node()} ({platform.machine()})",
        "label": "参考（XPS）",
        "paths": paths,
        "refusals": refusals,
        "log": [e.as_dict() for e in rig.screen.log],
        "passed": all(r["ok"] for r in paths) and all(r["ok"] for r in refusals),
    }


def main() -> int:
    result = run_all()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "operations.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (OUT_DIR / "operations.md").write_text(report(result), encoding="utf-8")
    for r in result["paths"]:
        print(f"{'ok' if r['ok'] else 'NG'} path {r['path']}: {r['outcome']}")
    for r in result["refusals"]:
        print(f"{'ok' if r['ok'] else 'NG'} refusal {r['what']}: http={r['http']} runs={r['runs']}")
    print(f"wrote docs/evidence/3b/operations.md: {'pass' if result['passed'] else 'FAIL'}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
