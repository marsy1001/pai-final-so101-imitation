"""Approval screen logic and its HTTP layer (Issue #27). No browser, no sim: the executor
is a fake that records what reached it (the real runner is in evidence_3b)."""

import json
import threading
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from so101_demo.execution.runner import REVIEW_APPROVAL_SOURCE
from so101_demo.plan import validate_plan
from so101_demo.ui import evidence_3b
from so101_demo.ui.screen import ActionRefused, ApprovalScreen, Phase
from so101_demo.ui.server import STATIC, create_app
from so101_demo.ui.sources import FixedSource, load_fixtures
from so101_demo.understanding.runs import RunGate

FIX = dict(load_fixtures())
F1 = FIX["F1-red_cube-box_a"]
F2 = FIX["F2-blue_cube-box_b"]


def non_ok(status):
    return validate_plan(
        {"status": status, "target": None, "destination": None, "steps": [], "note": "x"}
    )


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class FakeExecutor:
    """Records every approved plan; can hold a run until released."""

    def __init__(self, hold=False):
        self.calls = []
        self.release = threading.Event()
        if not hold:
            self.release.set()

    def __call__(self, approved):
        self.calls.append(approved)
        self.release.wait(5.0)
        return SimpleNamespace(outcome="success")


def make(hold=False, timeout_s=60.0):
    clock = Clock()
    ex = FakeExecutor(hold)
    screen = ApprovalScreen(
        executor=ex, gate=RunGate(timeout_s=timeout_s, clock=clock), clock=clock
    )
    return screen, ex, clock


def sid(screen):
    """The session the page would send: what view() shows now."""
    return screen.view()["session"]


def to_result(screen, plan=F1, sentence="s"):
    rid = screen.begin_reading()
    assert screen.deliver(rid, plan, sentence)
    return rid


# --- phases ---------------------------------------------------------------------------


def test_approve_path_idle_reading_result_running_idle():
    s, ex, _ = make(hold=True)
    assert s.phase is Phase.IDLE
    rid = s.begin_reading()
    assert s.phase is Phase.READING
    s.deliver(rid, F1, "赤を左へ")
    assert s.phase is Phase.RESULT
    s.approve(sid(s))
    assert s.phase is Phase.RUNNING
    ex.release.set()
    assert s.wait_idle(5)
    assert s.phase is Phase.IDLE
    (approved,) = ex.calls
    assert approved.plan == F1 and approved.reference == rid
    assert approved.source == REVIEW_APPROVAL_SOURCE
    assert s.log[-1].path == "承認" and not s.log[-1].rescued


def test_screen_shows_the_japanese_note_never_the_model_sentence():
    s, ex, _ = make(hold=True)
    to_result(s, F1, "The robot moves the red cube to the left tray.")
    v = s.view()
    assert v["reading"] == F1.note
    assert "The robot" not in json.dumps(v, ensure_ascii=False)
    s.approve(sid(s))
    assert s.view()["reading"] is None
    ex.release.set()


@pytest.mark.parametrize("status", ["unsupported", "needs_review", "error"])
def test_non_ok_result_goes_to_failed_and_cannot_be_approved(status):
    s, ex, _ = make()
    to_result(s, non_ok(status))
    assert s.phase is Phase.FAILED and s.view()["message"]
    with pytest.raises(ActionRefused):
        s.approve(sid(s))
    assert ex.calls == []


def test_over_the_wait_limit_goes_to_failed_and_the_late_result_is_dropped():
    s, ex, clock = make(timeout_s=5.0)
    rid = s.begin_reading()
    clock.t = 5.01
    assert s.view()["phase"] == Phase.FAILED.value
    assert not s.deliver(rid, F1)
    assert s.phase is Phase.FAILED and s.cur.plan is None
    assert ex.calls == []


def test_waiting_seconds_are_shown_while_reading():
    s, _, clock = make()
    s.begin_reading()
    clock.t = 2.34
    v = s.view()
    assert v["waiting_s"] == 2.3 and v["wait_limit_s"] == 60.0


def test_prerecorded_and_back():
    s, _, _ = make()
    s.prerecorded()
    assert s.phase is Phase.PRERECORDED and s.view()["actions"] == ["idle"]
    s.back_to_idle()
    assert s.phase is Phase.IDLE


def test_every_phase_has_a_label_and_actions():
    labels = {p.value for p in Phase}
    assert labels == {"待機", "読み取り中", "承認待ち", "拒否 / 失敗", "手動", "実行中", "事前録画"}


# --- the four paths -------------------------------------------------------------------


def test_correct_revalidates_and_logs_a_rescue():
    s, ex, _ = make()
    rid = to_result(s, F2)
    s.correct(sid(s), "blue_cube", "box_a")
    assert s.wait_idle(5)
    (approved,) = ex.calls
    assert (approved.plan.target, approved.plan.destination) == ("blue_cube", "box_a")
    assert approved.reference == rid
    e = s.log[-1]
    assert e.path == "訂正" and e.rescued
    assert e.shown["destination"] == "box_b" and e.final["destination"] == "box_a"


def test_correct_to_an_unregistered_id_is_refused():
    s, ex, _ = make()
    to_result(s, F2)
    with pytest.raises(ActionRefused):
        s.correct(sid(s), "green_cylinder", "box_a")
    assert s.phase is Phase.RESULT and ex.calls == []


def test_reject_runs_nothing_and_returns_to_idle():
    s, ex, _ = make()
    rid = to_result(s, non_ok("unsupported"))
    s.reject(sid(s))
    assert s.phase is Phase.IDLE and ex.calls == []
    assert s.log[-1].path == "拒否" and s.log[-1].final is None
    assert not s.deliver(rid, F1)  # the rejected run is closed


def test_manual_from_error_chooses_a_candidate():
    s, ex, _ = make()
    to_result(s, non_ok("error"))
    s.enter_manual()
    assert s.phase is Phase.MANUAL and len(s.view()["candidates"]) == 4
    s.choose(sid(s), "red_cube", "box_b")
    assert s.wait_idle(5)
    (approved,) = ex.calls
    assert approved.reference.startswith("manual-")
    assert s.log[-1].path == "手動" and s.log[-1].rescued


def test_manual_without_any_reading():
    s, ex, _ = make()
    s.enter_manual()
    s.choose(sid(s), "blue_cube", "box_b")
    assert s.wait_idle(5) and len(ex.calls) == 1


# --- what must never be approved -------------------------------------------------------


def test_nothing_is_approvable_while_reading():
    s, ex, _ = make()
    s.begin_reading()
    for op in (
        lambda: s.approve(sid(s)),
        lambda: s.correct(sid(s), "red_cube", "box_a"),
        lambda: s.reject(sid(s)),
    ):
        with pytest.raises(ActionRefused):
            op()
    assert ex.calls == [] and s.phase is Phase.READING


def test_late_result_after_manual_neither_shows_nor_runs():
    s, ex, _ = make()
    rid = s.begin_reading()
    s.enter_manual()
    assert not s.deliver(rid, F1, "late")
    assert s.phase is Phase.MANUAL and s.view()["target"] is None
    with pytest.raises(ActionRefused):
        s.approve(sid(s))
    assert ex.calls == []
    assert any(e.run_id == rid and e.result == "dropped: cancelled" for e in s.log)


def test_result_of_a_replaced_reading_is_dropped():
    s, ex, _ = make()
    old = s.begin_reading()
    s.back_to_idle()
    new = s.begin_reading()
    assert not s.deliver(old, F2)
    assert s.deliver(new, F1)
    assert s.view()["target"]["id"] == "red_cube"


def test_double_approve_runs_once():
    s, ex, _ = make(hold=True)
    to_result(s)
    s.approve(sid(s))
    with pytest.raises(ActionRefused):
        s.approve(sid(s))  # while running
    ex.release.set()
    assert s.wait_idle(5)
    with pytest.raises(ActionRefused):
        s.approve(sid(s))  # after the run
    assert len(ex.calls) == 1


def test_no_operation_but_view_while_running():
    s, ex, _ = make(hold=True)
    to_result(s)
    s.approve(sid(s))
    for op in (
        s.begin_reading,
        s.enter_manual,
        s.prerecorded,
        s.back_to_idle,
        lambda: s.reject(sid(s)),
        lambda: s.choose(sid(s), "red_cube", "box_a"),
    ):
        with pytest.raises(ActionRefused):
            op()
    assert s.view()["actions"] == []
    ex.release.set()
    assert s.wait_idle(5) and len(ex.calls) == 1


def test_executor_error_is_logged_and_the_screen_returns_to_idle():
    def boom(_approved):
        raise RuntimeError("sim died")

    s = ApprovalScreen(executor=boom)
    to_result(s)
    s.approve(sid(s))
    assert s.wait_idle(5) and s.phase is Phase.IDLE
    assert s.log[-1].result.startswith("error: RuntimeError")
    assert s.view()["last_result"] == "うまくいきませんでした"


def test_refused_operations_are_logged():
    s, _, _ = make()
    with pytest.raises(ActionRefused):
        s.approve(sid(s))
    assert s.log[-1].result.startswith("refused:")


# --- HTTP -----------------------------------------------------------------------------


class NowSource:
    def __init__(self, *plans):
        self.plans = list(plans)

    def start(self, run_id, deliver):
        deliver(run_id, self.plans.pop(0), "文")


def client_for(screen, source=None, frames=None):
    return TestClient(create_app(screen, source, frames))


def cpost(c, op, **body):
    """POST as the page does: with the session of the view it is showing."""
    body.setdefault("session", c.get("/api/view").json()["session"])
    return c.post(f"/api/{op}", json=body)


def test_http_four_paths_and_refusals():
    s, ex, _ = make()
    c = client_for(s, NowSource(F1, F2, non_ok("unsupported"), non_ok("error")))
    assert cpost(c, "approve").status_code == 409
    assert c.post("/api/read").json()["phase"] == "承認待ち"
    assert cpost(c, "approve").status_code == 200 and s.wait_idle(5)
    c.post("/api/read")
    r = cpost(c, "correct", target="blue_cube", destination="box_a")
    assert r.status_code == 200 and s.wait_idle(5)
    c.post("/api/read")
    assert cpost(c, "reject").json()["phase"] == "待機"
    c.post("/api/read")
    assert c.post("/api/manual").json()["phase"] == "手動"
    r = cpost(c, "choose", target="red_cube", destination="box_b")
    assert r.status_code == 200 and s.wait_idle(5)
    assert [(a.plan.target, a.plan.destination) for a in ex.calls] == [
        ("red_cube", "box_a"),
        ("blue_cube", "box_a"),
        ("red_cube", "box_b"),
    ]
    assert [e.path for e in s.log if not e.result.startswith("refused")] == [
        "承認",
        "訂正",
        "拒否",
        "手動",
    ]


def test_http_read_without_a_source_is_refused():
    s, _, _ = make()
    assert client_for(s).post("/api/read").status_code == 409


def test_http_bad_pair_is_refused_without_running():
    s, ex, _ = make()
    c = client_for(s, NowSource(F1))
    c.post("/api/read")
    r = cpost(c, "correct", target="red_cube", destination="box_z")
    assert r.status_code == 409 and ex.calls == []


def test_frame_is_503_without_rendering_and_served_when_present():
    s, _, _ = make()
    assert client_for(s).get("/frame.jpg").status_code == 503

    class Frames:
        def latest(self):
            return b"\xff\xd8jpeg"

    r = client_for(s, frames=Frames()).get("/frame.jpg")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"


def test_websocket_pushes_the_view_on_change():
    s, _, _ = make()
    c = client_for(s, NowSource(F1))
    with c.websocket_connect("/ws") as ws:
        assert ws.receive_json()["phase"] == "待機"
        c.post("/api/read")
        assert ws.receive_json()["phase"] == "承認待ち"


def test_fixed_source_replays_the_4a_fixtures_in_turn():
    got = []
    done = threading.Event()

    def deliver(run_id, plan, sentence):
        got.append((run_id, plan.target, plan.destination))
        if len(got) == 5:
            done.set()
        return True

    src = FixedSource(delay_s=0.0)
    for i in range(5):
        src.start(f"r{i}", deliver)
    assert done.wait(5)
    assert sorted(got)[0] == ("r0", "red_cube", "box_a")
    assert sorted(got)[4] == ("r4", "red_cube", "box_a")  # wraps around after F4


# --- nothing raw on screen ------------------------------------------------------------

FORBIDDEN = ("submit_plan", "astra", "cosmos", "status")


def test_served_pages_show_no_raw_json_or_api_names():
    s, _, _ = make()
    c = client_for(s)
    bodies = [c.get("/").text, c.get("/big").text]
    bodies += [c.get(f"/static/{p.name}").text for p in STATIC.iterdir()]
    for body in bodies:
        low = body.lower()
        for word in FORBIDDEN:
            assert word not in low, word


def test_view_in_every_phase_has_no_raw_names_or_plan():
    s, ex, clock = make(hold=True)
    views = [s.view()]
    rid = s.begin_reading()
    views.append(s.view())
    s.deliver(rid, F1, "赤いキューブを左の箱へ")
    views.append(s.view())
    s.approve(sid(s))
    views.append(s.view())
    ex.release.set()
    s.wait_idle(5)
    to_result(s, non_ok("error"))
    views.append(s.view())
    s.enter_manual()
    views.append(s.view())
    s.back_to_idle()
    s.prerecorded()
    views.append(s.view())
    assert {v["phase"] for v in views} == {p.value for p in Phase}
    for v in views:
        assert "steps" not in v and "note" not in v
        text = str(v).lower()
        for word in FORBIDDEN:
            assert word not in text, (v["phase"], word)


def test_labels_and_colours_come_from_the_registry():
    s, _, _ = make()
    to_result(s)
    v = s.view()
    assert v["target"] == {
        "id": "red_cube",
        "name": "赤いキューブ",
        "label": "R",
        "text": "赤いキューブ R",
        "css": "#d91a1a",
    }
    assert v["destination"]["text"] == "箱 A" and v["destination"]["css"] == "#8c8c8c"
    for item in v["objects"] + v["destinations"]:  # the registry label is on screen
        assert item["text"].endswith(" " + item["label"])


def test_chips_draw_the_text_with_the_label():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "document.createTextNode(item.text)" in js and "item.name" not in js


# --- evidence -------------------------------------------------------------------------


def test_evidence_run_passes_with_the_real_runner():
    result = evidence_3b.run_all()
    assert result["passed"], [r for r in result["paths"] + result["refusals"] if not r["ok"]]
    assert [r["path"] for r in result["paths"]] == ["承認", "訂正", "拒否", "手動"]
    text = evidence_3b.report(result)
    assert text.startswith("# ") and "\n## 結論\n" in text and "参考" in text


# --- runner thread --------------------------------------------------------------------


def test_runner_thread_builds_and_submits_on_one_thread():
    from so101_demo.ui.runner_thread import RunnerThread

    seen = []

    class FakeRunner:
        def __init__(self):
            seen.append(("build", threading.get_ident()))

        def submit(self, approved):
            seen.append(("submit", threading.get_ident()))
            if approved == "boom":
                raise RuntimeError("x")
            return SimpleNamespace(outcome="success")

    rt = RunnerThread(FakeRunner)
    assert rt.submit("a").outcome == "success"
    with pytest.raises(RuntimeError):
        rt.submit("boom")
    assert len({tid for _, tid in seen}) == 1 and seen[0][1] != threading.get_ident()


def test_runner_thread_build_failure_reaches_the_caller():
    from so101_demo.ui.runner_thread import RunnerThread

    def fail():
        raise ValueError("no GL")

    with pytest.raises(ValueError, match="no GL"):
        RunnerThread(fail)


# --- review findings (running shows the plan that runs; no dead 訂正; failed source) ---


@pytest.mark.parametrize(
    "setup",
    [
        lambda s: (to_result(s, F2), s.correct(sid(s), "blue_cube", "box_a")),
        lambda s: (to_result(s, F2), s.enter_manual(), s.choose(sid(s), "blue_cube", "box_a")),
        lambda s: (s.enter_manual(), s.choose(sid(s), "blue_cube", "box_a")),
    ],
    ids=["corrected", "manual-from-result", "manual-from-idle"],
)
def test_running_shows_the_plan_that_runs(setup):
    s, ex, _ = make(hold=True)
    setup(s)
    v = s.view()
    assert v["phase"] == "実行中"
    assert (v["target"]["id"], v["destination"]["id"]) == ("blue_cube", "box_a")
    ex.release.set()
    assert s.wait_idle(5)


def test_correct_is_not_offered_after_the_wait_limit():
    s, _, clock = make(timeout_s=1.0)
    s.begin_reading()
    clock.t = 2.0
    v = s.view()
    assert v["phase"] == "拒否 / 失敗" and "correct" not in v["actions"]
    assert "manual" in v["actions"]


def test_correct_is_offered_on_a_shown_non_ok_result():
    s, _, _ = make()
    to_result(s, non_ok("needs_review"))
    assert "correct" in s.view()["actions"]


def test_http_read_with_a_failing_source_returns_to_idle():
    class Broken:
        def start(self, run_id, deliver):
            raise RuntimeError("camera gone")

    s, _, _ = make()
    c = client_for(s, Broken())
    assert c.post("/api/read").status_code == 503
    assert s.phase is Phase.IDLE and s.gate.current is None
    assert s.log[-1].result.startswith("dropped: source failed")
    assert c.post("/api/manual").status_code == 200


# --- stale pages (Codex review 1, #27): an operation names the session it saw --------


def test_stale_page_cannot_approve_the_next_result():
    s, ex, _ = make()
    c = client_for(s, NowSource(F1, F2))
    c.post("/api/read")
    page_a = c.get("/api/view").json()  # page A shows F1
    assert page_a["target"]["id"] == "red_cube"
    c.post("/api/idle")  # page B cancels and reads again: F2 on screen
    c.post("/api/read")
    r = c.post("/api/approve", json={"session": page_a["session"]})
    assert r.status_code == 409 and ex.calls == []
    assert s.phase is Phase.RESULT and s.view()["target"]["id"] == "blue_cube"


@pytest.mark.parametrize("op", ["approve", "reject"])
def test_operation_without_a_session_is_refused(op):
    s, ex, _ = make()
    c = client_for(s, NowSource(F1))
    c.post("/api/read")
    assert c.post(f"/api/{op}", json={}).status_code == 409
    assert s.phase is Phase.RESULT and ex.calls == []


def test_stale_page_cannot_correct_the_next_result():
    s, ex, _ = make()
    c = client_for(s, NowSource(F1, F2))
    c.post("/api/read")
    old = c.get("/api/view").json()["session"]
    c.post("/api/idle")
    c.post("/api/read")
    r = c.post("/api/correct", json={"session": old, "target": "red_cube", "destination": "box_b"})
    assert r.status_code == 409 and ex.calls == []


def test_stale_manual_page_cannot_choose_in_a_new_manual_session():
    s, ex, _ = make()
    c = client_for(s)
    c.post("/api/manual")
    old = c.get("/api/view").json()["session"]
    c.post("/api/idle")
    c.post("/api/manual")
    body = {"session": old, "target": "red_cube", "destination": "box_a"}
    assert c.post("/api/choose", json=body).status_code == 409 and ex.calls == []
    assert cpost(c, "choose", target="red_cube", destination="box_a").status_code == 200


def test_session_is_shown_only_where_an_operation_needs_it():
    s, _, _ = make(hold=True)
    assert s.view()["session"] is None
    rid = s.begin_reading()
    assert s.view()["session"] is None
    s.deliver(rid, F1)
    assert s.view()["session"] == rid
    s.enter_manual()
    assert s.view()["session"].startswith("manual-")


# --- the real server (Codex review 1, #27): WebSocket through uvicorn ---------------


def test_real_uvicorn_upgrades_the_websocket_and_pushes_after_an_operation():
    import asyncio
    import json
    import socket
    import urllib.request

    import uvicorn
    import websockets

    s, _, _ = make()
    app = create_app(s, NowSource(F1))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    try:
        for _ in range(100):
            if server.started:
                break
            threading.Event().wait(0.05)
        assert server.started

        async def talk():
            async with websockets.connect(f"ws://127.0.0.1:{port}/ws") as ws:
                assert ws.response.status_code == 101
                first = json.loads(await ws.recv())
                req = urllib.request.Request(f"http://127.0.0.1:{port}/api/read", method="POST")
                await asyncio.to_thread(urllib.request.urlopen, req)
                second = json.loads(await asyncio.wait_for(ws.recv(), 5))
                return first, second

        first, second = asyncio.run(talk())
        assert first["phase"] == "待機" and second["phase"] == "承認待ち"
    finally:
        server.should_exit = True
        t.join(5)


# --- the page never draws an older view (Codex review 2, #27) -------------------------

NODE = __import__("shutil").which("node")
HARNESS = __import__("pathlib").Path(__file__).parent / "js" / "view_order.js"


def run_page(tmp_path, scenario):
    import json
    import subprocess

    path = tmp_path / "scenario.json"
    path.write_text(json.dumps(scenario, ensure_ascii=False), encoding="utf-8")
    out = subprocess.run(
        [NODE, str(HARNESS), str(STATIC / "app.js"), str(path)],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    ).stdout
    return [json.loads(line) for line in out.splitlines()]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_late_http_answer_does_not_take_the_page_back(tmp_path):
    s, ex, _ = make(hold=True)
    to_result(s)
    v_result = s.view()
    s.approve(sid(s))
    v_running = s.view()  # what the POST answers, delayed below
    ex.release.set()
    assert s.wait_idle(5)
    v_idle = s.view()
    assert v_result["version"] < v_running["version"] < v_idle["version"]
    shown = run_page(
        tmp_path,
        [
            {"do": "get", "view": v_result},
            {"do": "click", "id": "approve"},
            {"do": "push", "view": v_running},
            {"do": "push", "view": v_idle},
            {"do": "answer", "view": v_running},
        ],
    )
    assert shown[3]["phase"] == "待機" and shown[3]["read_enabled"]
    assert shown[-1]["phase"] == "待機" and shown[-1]["read_enabled"]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_late_reading_answer_does_not_hide_the_result(tmp_path):
    s, _, _ = make()
    v_idle = s.view()
    rid = s.begin_reading()
    v_reading = s.view()
    s.deliver(rid, F1)
    v_result = s.view()
    shown = run_page(
        tmp_path,
        [
            {"do": "get", "view": v_idle},
            {"do": "click", "id": "read"},
            {"do": "push", "view": v_result},
            {"do": "answer", "view": v_reading},
        ],
    )
    assert shown[-1]["phase"] == "承認待ち" and shown[-1]["approve_enabled"]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_restarted_server_is_drawn_although_its_version_is_lower(tmp_path):
    s, ex, _ = make()
    to_result(s)
    old = s.view()
    fresh = make()[0].view()  # a new server: version 0, another boot
    assert fresh["version"] < old["version"] and fresh["boot"] != old["boot"]
    shown = run_page(tmp_path, [{"do": "get", "view": old}, {"do": "push", "view": fresh}])
    assert shown[0]["phase"] == "承認待ち" and shown[-1]["phase"] == "待機"


def test_version_grows_with_every_change():
    s, ex, _ = make()
    seen = [s.view()["version"]]
    rid = s.begin_reading()
    seen.append(s.view()["version"])
    s.deliver(rid, F1)
    seen.append(s.view()["version"])
    s.approve(sid(s))
    s.wait_idle(5)
    seen.append(s.view()["version"])
    assert seen == sorted(seen) and len(set(seen)) == len(seen)


def test_uc_results_hand_the_screen_every_acceptance_row():
    from so101_demo.ui.evidence_3b_gpu import uc_results

    uc = uc_results()
    assert sorted(uc) == [f"uc-{n:02d}" for n in range(1, 16)]
    # the rows the GPU evidence drives: right, swapped, needs_review, unsupported,
    # false accept, and a Cosmos failure
    assert (uc["uc-02"]["plan"].target, uc["uc-02"]["plan"].destination) == ("red_cube", "box_a")
    assert uc["uc-04"]["plan"].destination != uc["uc-04"]["gold"]["destination"]
    assert uc["uc-01"]["plan"].status == "needs_review"
    assert uc["uc-13"]["plan"].status == "unsupported"
    assert uc["uc-15"]["plan"].executable and uc["uc-15"]["scope"] != "in_scope"
    assert uc["uc-07"]["plan"].status == "error"


def test_committed_gpu_operations_report_is_rendered_from_its_json():
    from so101_demo.ui.evidence_3b_gpu import OUT_DIR, report

    path = OUT_DIR / "operations-gpu.json"
    if not path.exists():
        pytest.skip("GPU evidence not generated")
    result = json.loads(path.read_text(encoding="utf-8"))
    assert (OUT_DIR / "operations-gpu.md").read_text(encoding="utf-8") == report(result)
