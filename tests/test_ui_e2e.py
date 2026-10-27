"""The real understanding chain on the approval screen, the run log and the end-to-end
evidence checker. No network: Cosmos and Astra are a fake `post` injected
through `understand(**kw)`. No sim and no rendering: the executor and the recordings are
fakes."""

import json
import shutil
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from test_understanding import ENV, OK_ARGS, VIDEO, FakePost, call, chat_body, responses_body

from so101_demo.plan.paths import REPO_ROOT, UC_MANIFEST_PATH
from so101_demo.ui import evidence_e2e
from so101_demo.ui.recording import Recording
from so101_demo.ui.runs_log import REQUIRED_KEYS, RunsLog, read_rows
from so101_demo.ui.screen import ApprovalScreen, Phase
from so101_demo.ui.server import create_app
from so101_demo.ui.sources import Video, VideoSource, load_videos
from so101_demo.understanding import settings as S
from so101_demo.understanding.transport import TransportError

VIDEOS = [Video("s1-01", VIDEO)]
SENTENCE = "The hand moves the red cube to the tray on the left."
COSMOS_URL = "http://cosmos.test"


def cosmos_ok(sentence=SENTENCE):
    return chat_body(f"<think>looking</think>{sentence}")


def astra_ok():
    return responses_body(call(OK_ARGS))


class HoldPost(FakePost):
    """Blocks the first request until `release` is set (a slow Cosmos)."""

    def __init__(self, *responses):
        super().__init__(*responses)
        self.started = threading.Event()
        self.release = threading.Event()

    def __call__(self, url, headers, body, timeout):
        if not self.started.is_set():
            self.started.set()
            assert self.release.wait(5.0)
        return super().__call__(url, headers, body, timeout)


class FakeExecutor:
    def __init__(self, recordings=None):
        self.calls = []
        self.recordings = recordings

    def __call__(self, approved):
        self.calls.append(approved)
        if self.recordings is not None:
            p = approved.plan
            self.recordings[approved.reference] = Recording(
                f"{approved.reference}-{p.target}-{p.destination}.mp4",
                1_234_567, 300, 31.5, 0.3, 0.032, 12.0, "h264_nvenc", None,
            )  # fmt: skip
        return SimpleNamespace(outcome="success", commands_sent=7, wall_time_s=1.5, reason=None)


def make(post, executor=None, on_log=None, videos=VIDEOS):
    """A screen with the real `VideoSource` (fake network); the first video selected, as
    the operator does before 読み取り開始."""
    ex = executor or FakeExecutor()
    source = VideoSource(videos, post=post, env=ENV, base_url=COSMOS_URL)
    screen = ApprovalScreen(executor=ex, on_log=on_log, videos=source.ids)
    screen.select(videos[0].id)
    return screen, source, ex, TestClient(create_app(screen, source, None, source.files))


def until(pred, timeout=5.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


def sid(client):
    return client.get("/api/view").json()["session"]


# --- (a) a result reaches the screen as 承認待ち ----------------------------------------


def test_video_source_result_reaches_the_screen_as_a_result():
    post = HoldPost(cosmos_ok(), astra_ok())  # held: the screen is seen while reading
    screen, source, ex, c = make(post)
    v = c.post("/api/read").json()
    assert v["phase"] == "読み取り中" and v["video"] == "s1-01"
    assert post.started.wait(5)
    post.release.set()
    assert until(lambda: screen.phase is Phase.RESULT)
    v = c.get("/api/view").json()
    assert v["target"]["id"] == "red_cube" and v["destination"]["id"] == "box_a"
    assert v["reading"] == OK_ARGS["note"] and v["video"] == "s1-01"
    # the real chain ran: Cosmos first (the video as a file URL), then Astra
    assert [x["url"] for x in post.calls] == [
        f"{COSMOS_URL}/v1/chat/completions",
        ENV["AOAI_ENDPOINT"].rstrip("/") + "/openai/v1/responses",
    ]
    assert post.calls[0]["body"]["messages"][0]["content"][0]["video_url"]["url"].endswith(
        VIDEO.name
    )
    run = source.run_of(screen.cur.run_id)
    assert run.video.id == "s1-01" and run.understanding.cosmos.sentence == SENTENCE
    assert run.understanding.run_id == screen.cur.run_id
    assert ex.calls == []  # nothing runs before a person approves


def test_video_source_reads_the_selected_video_and_only_registered_ids():
    seen = []
    done = threading.Event()

    def understand(video, run_id=None, **kw):
        seen.append((run_id, video))
        if len(seen) == 3:
            done.set()
        raise RuntimeError("stop here")

    src = VideoSource([Video("a", VIDEO), Video("b", VIDEO)], understand=understand)
    assert src.ids == ["a", "b"]
    delivered = []
    for i, vid in enumerate(["b", "b", "a"]):
        assert src.start(f"r{i}", lambda *a: delivered.append(a) or True, vid) == vid
    assert done.wait(5) and [r for r, _ in seen] == ["r0", "r1", "r2"]
    assert until(lambda: len(delivered) == 3)
    assert all(plan.status == "error" for _, plan, _ in delivered)
    assert src.run_of("r0").error.startswith("RuntimeError")
    assert [src.run_of(f"r{i}").video.id for i in range(3)] == ["b", "b", "a"]
    with pytest.raises(ValueError, match="not a registered video: 'zz'"):
        src.start("r9", lambda *a: True, "zz")
    with pytest.raises(ValueError, match="not a registered video: None"):
        src.start("r9", lambda *a: True, None)


# --- (e) the operator selects the video; the big screen follows -------------

TWO = [Video("uc-02", VIDEO), Video("uc-13", VIDEO)]


def big(c):
    return c.get("/api/view").json()["big"]


def test_select_then_read_sends_the_selected_video_to_understand():
    seen = []
    go = threading.Event()  # the chain waits, so 読み取り中 can be seen

    def understand(video, run_id=None, **kw):
        seen.append(video)
        go.wait(5.0)
        raise RuntimeError("stop here")

    source = VideoSource(TWO, understand=understand)
    screen = ApprovalScreen(executor=FakeExecutor(), videos=source.ids)
    c = TestClient(create_app(screen, source, None, source.files))
    v = c.get("/api/view").json()
    assert v["videos"] == [{"id": "uc-02"}, {"id": "uc-13"}] and v["selected"] is None
    assert "select" in v["actions"]
    # nothing selected: 読み取り開始 is refused and the chain is not called
    assert c.post("/api/read").status_code == 409 and seen == []
    assert screen.phase is Phase.IDLE
    # an unregistered id is refused; the screen keeps no selection
    assert c.post("/api/select", json={"video_id": "uc-99"}).status_code == 409
    assert c.get("/api/view").json()["selected"] is None
    v = c.post("/api/select", json={"video_id": "uc-13"}).json()
    assert v["selected"] == "uc-13" and v["big"] == {"kind": "video", "video": "uc-13"}
    v = c.post("/api/read").json()
    assert v["phase"] == "読み取り中" and v["video"] == "uc-13"
    # selecting is only for 待機
    assert c.post("/api/select", json={"video_id": "uc-02"}).status_code == 409
    go.set()
    assert until(lambda: screen.phase is Phase.FAILED)
    assert seen == [VIDEO] and source.run_of(screen.cur.run_id).video.id == "uc-13"
    assert c.post("/api/select", json={"video_id": "uc-02"}).status_code == 409
    assert cpost(c, "reject").json()["phase"] == "待機"
    assert c.get("/api/view").json()["selected"] == "uc-13"  # kept for the next reading


def test_big_screen_shows_the_video_until_the_robot_runs():
    post = HoldPost(cosmos_ok(), astra_ok())
    ex = FakeExecutor()
    ex.hold = threading.Event()
    held = ex.__call__

    def slow(approved):
        ex.hold.wait(5.0)
        return held(approved)

    screen, source, _, c = make(post, executor=slow, videos=TWO)
    assert screen.phase is Phase.IDLE and big(c) == {"kind": "video", "video": "uc-02"}
    c.post("/api/read")
    assert post.started.wait(5)
    assert big(c) == {"kind": "video", "video": "uc-02"}  # 読み取り中
    post.release.set()
    assert until(lambda: screen.phase is Phase.RESULT)
    assert big(c) == {"kind": "video", "video": "uc-02"}  # 承認待ち
    assert cpost(c, "approve").status_code == 200
    assert screen.phase is Phase.RUNNING and big(c) == {"kind": "sim", "video": None}
    ex.hold.set()
    assert screen.wait_idle(5)
    assert big(c) == {"kind": "video", "video": "uc-02"}  # 待機, still selected
    # 手動 and 事前録画 show the sim; back in 待機 the selection is still on
    assert c.post("/api/manual").json()["big"] == {"kind": "sim", "video": None}
    assert c.post("/api/idle").json()["big"]["kind"] == "video"
    assert c.post("/api/prerecorded").json()["big"]["kind"] == "sim"


def test_big_screen_shows_the_video_of_a_refused_reading():
    post = FakePost(TransportError("connection failed"))
    screen, source, ex, c = make(post, videos=TWO)
    c.post("/api/read")
    assert until(lambda: screen.phase is Phase.FAILED)
    assert big(c) == {"kind": "video", "video": "uc-02"}


def test_big_screen_is_sim_without_a_selection_and_with_fixed_jsons():
    source = VideoSource(TWO, understand=lambda *a, **k: None)
    screen = ApprovalScreen(executor=FakeExecutor(), videos=source.ids)
    c = TestClient(create_app(screen, source, None, source.files))
    assert big(c) == {"kind": "sim", "video": None}
    # fixed JSONs: no list, nothing to select, the reading's label is not a video
    from so101_demo.ui.sources import FixedSource

    fixed = ApprovalScreen(executor=FakeExecutor())
    cf = TestClient(create_app(fixed, FixedSource(delay_s=0.0)))
    v = cf.get("/api/view").json()
    assert v["videos"] == [] and "select" not in v["actions"]
    assert cf.post("/api/select", json={"video_id": "F1-red_cube-box_a"}).status_code == 409
    v = cf.post("/api/read").json()
    assert v["phase"] in ("読み取り中", "承認待ち") and v["big"] == {"kind": "sim", "video": None}
    assert until(lambda: fixed.phase is Phase.RESULT)
    assert cf.get("/api/view").json()["big"] == {"kind": "sim", "video": None}


def test_videos_are_served_only_when_registered():
    source = VideoSource(TWO, understand=lambda *a, **k: None)
    screen = ApprovalScreen(executor=FakeExecutor(), videos=source.ids)
    c = TestClient(create_app(screen, source, None, source.files))
    r = c.get("/videos/uc-02")
    assert r.status_code == 200 and r.headers["content-type"] == "video/mp4"
    assert r.content[:12] == VIDEO.read_bytes()[:12]
    assert c.head("/videos/uc-02").status_code == 200  # what `curl -I` sends
    assert c.get("/videos/uc-99").status_code == 404
    assert c.get("/videos/../pyproject.toml").status_code == 404
    # fixed JSONs: nothing is served
    from so101_demo.ui.sources import FixedSource

    cf = TestClient(create_app(ApprovalScreen(executor=FakeExecutor()), FixedSource()))
    assert cf.get("/videos/uc-02").status_code == 404
    assert cf.get("/videos/F1-red_cube-box_a").status_code == 404
    # without an explicit map the source's own videos are served (Codex-style review 1)
    c2 = TestClient(create_app(screen, source))
    assert c2.get("/videos/uc-13").status_code == 200
    assert TestClient(create_app(screen, source, None, {})).get("/videos/uc-13").status_code == 404


def test_runs_log_keeps_the_selected_video_id(tmp_path):
    ex = FakeExecutor({})
    post = FakePost(cosmos_ok(), astra_ok())
    source = VideoSource(TWO, post=post, env=ENV, base_url=COSMOS_URL)
    log = RunsLog(tmp_path / "runs.jsonl", source=source, recordings=ex.recordings)
    screen = ApprovalScreen(executor=log.executor(ex), on_log=log, videos=source.ids)
    c = TestClient(create_app(screen, source, None, source.files))
    assert c.post("/api/select", json={"video_id": "uc-02"}).status_code == 200
    c.post("/api/read")
    assert until(lambda: screen.phase is Phase.RESULT)
    assert cpost(c, "approve").status_code == 200 and screen.wait_idle(5)
    (row,) = read_rows(tmp_path / "runs.jsonl")
    assert row["video_id"] == "uc-02" and row["outcome"] == "success"


# --- (b) a late result after 手動 / 待機へ戻る / 対応外 is dropped --------------------------


@pytest.mark.parametrize("op", ["manual", "idle"])
def test_result_arriving_after_the_operator_moved_on_is_dropped(op):
    post = HoldPost(cosmos_ok(), astra_ok())
    screen, source, ex, c = make(post)
    c.post("/api/read")
    assert post.started.wait(5)
    rid = screen.cur.run_id
    assert c.post(f"/api/{op}").status_code == 200
    post.release.set()
    dropped = lambda: any(  # noqa: E731
        e.run_id == rid and e.result == "dropped: cancelled" for e in screen.log
    )
    assert until(dropped)
    v = c.get("/api/view").json()
    assert v["phase"] == ("手動" if op == "manual" else "待機") and v["target"] is None
    assert source.run_of(rid).understanding.plan.status == "ok"  # the chain did finish
    assert cpost(c, "approve").status_code == 409 and ex.calls == []


def cpost(c, op, **body):
    body.setdefault("session", sid(c))
    return c.post(f"/api/{op}", json=body)


def test_reject_while_reading_is_refused_and_rejects_the_result_afterwards():
    post = HoldPost(cosmos_ok(), astra_ok())
    screen, source, ex, c = make(post)
    c.post("/api/read")
    assert post.started.wait(5)
    assert cpost(c, "reject").status_code == 409  # nothing on screen yet
    post.release.set()
    assert until(lambda: screen.phase is Phase.RESULT)
    assert cpost(c, "reject").json()["phase"] == "待機"
    assert ex.calls == [] and screen.log[-1].path == "拒否"


# --- (c) a Cosmos failure is 拒否 / 失敗 and nothing reaches the runner ---------------------


@pytest.mark.parametrize(
    "post",
    [
        lambda: FakePost(chat_body("<think>never ends", finish="length")),
        lambda: FakePost(TransportError("connection failed")),
    ],
    ids=["runaway", "transport"],
)
def test_cosmos_failure_is_failed_on_screen_and_astra_is_not_called(post):
    post = post()
    screen, source, ex, c = make(post)
    c.post("/api/read")
    assert until(lambda: screen.phase is Phase.FAILED)
    v = c.get("/api/view").json()
    assert v["phase"] == "拒否 / 失敗" and v["message"] == "読み取りに失敗しました"
    assert len(post.calls) == 1  # Cosmos only
    u = source.run_of(screen.cur.run_id).understanding
    assert u.failed_stage == "cosmos" and u.astra is None
    assert cpost(c, "approve").status_code == 409 and ex.calls == []
    assert "approve" not in v["actions"] and "correct" in v["actions"]


def test_source_that_cannot_call_the_chain_is_failed_on_screen():
    # Cosmos answers, then Astra has no keys (.env missing): `understand` raises
    source = VideoSource(VIDEOS, post=FakePost(cosmos_ok()), env={})
    screen = ApprovalScreen(executor=FakeExecutor(), videos=source.ids)
    c = TestClient(create_app(screen, source))
    c.post("/api/select", json={"video_id": "s1-01"})
    c.post("/api/read")
    assert until(lambda: screen.phase is Phase.FAILED)
    assert source.run_of(screen.cur.run_id).error.startswith("RuntimeError")


# --- (d) 承認 → 実行 writes one line with every required key --------------------------


def test_approved_run_and_rejected_run_each_write_one_line(tmp_path):
    recordings = {}
    ex = FakeExecutor(recordings)
    post = FakePost(cosmos_ok(), astra_ok(), cosmos_ok("Moves a pen."), responses_body(
        call({**OK_ARGS, "status": "unsupported", "target": None, "destination": None,
              "steps": [], "note": "ペンは対応外"})
    ))  # fmt: skip
    source = VideoSource(VIDEOS, post=post, env=ENV, base_url=COSMOS_URL)
    log = RunsLog(
        tmp_path / "runs.jsonl",
        source=source,
        recordings=recordings,
        session=lambda: {"commit": "abc1234", "machine": {"gpu": "fake"}},
    )
    screen = ApprovalScreen(executor=log.executor(ex), on_log=log, videos=source.ids)
    c = TestClient(create_app(screen, source))
    c.post("/api/select", json={"video_id": "s1-01"})
    c.post("/api/read")
    assert until(lambda: screen.phase is Phase.RESULT)
    assert cpost(c, "approve").status_code == 200 and screen.wait_idle(5)
    c.post("/api/read")  # the selection is kept
    assert until(lambda: screen.phase is Phase.FAILED)
    assert cpost(c, "reject").status_code == 200
    rows = read_rows(tmp_path / "runs.jsonl")
    assert len(rows) == 2 and screen.log_errors == []
    ok, rej = rows
    for r in rows:
        assert all(k in r for k in REQUIRED_KEYS)
        assert r["video_id"] == "s1-01" and r["video_path"] == str(VIDEO.relative_to(REPO_ROOT))
        assert r["session"]["commit"] == "abc1234"
    assert ok["operation"] == "承認" and ok["outcome"] == "success" and not ok["rescued"]
    assert ok["sentence"] == SENTENCE and ok["plan"]["target"] == "red_cube"
    assert ok["final"] == ok["plan"] and ok["commands_sent"] == 7
    assert ok["mp4"] == f"{ok['run_id']}-red_cube-box_a.mp4"
    assert ok["recording"]["effective_fps"] == 31.5 and ok["recording"]["encoder"] == "h264_nvenc"
    assert ok["elapsed_s"]["run"] == 1.5 and ok["elapsed_s"]["understanding"] >= 0
    assert ok["elapsed_s"]["total"] == round(ok["elapsed_s"]["understanding"] + 1.5, 2)
    assert rej["operation"] == "拒否" and rej["outcome"] == "not run"
    assert rej["plan"]["status"] == "unsupported" and rej["commands_sent"] == 0
    assert rej["mp4"] is None and rej["recording"] is None and rej["final"] is None
    # refused operations are not runs
    assert cpost(c, "approve").status_code == 409
    assert len(read_rows(tmp_path / "runs.jsonl")) == 2


def test_recorder_that_cannot_start_does_not_decide_the_run(tmp_path):
    """No renderer (the observer never ran) and no sim: the recorder cannot be built; the
    run still goes through and the row says why there is no video."""
    from so101_demo.execution.runner import approve_from_review
    from so101_demo.ui.frames import FrameStore
    from so101_demo.ui.recording import ScreenRecorder

    sr = ScreenRecorder(FrameStore(), tmp_path / "rec")
    approved = approve_from_review(source_plan(), "run1")
    runner = SimpleNamespace(
        registry=None,
        submit=lambda a: SimpleNamespace(outcome="success", observer_error=None, commands_sent=3),
    )
    rec = sr.run(runner, approved)
    assert rec.outcome == "success"
    r = sr.recording_of("run1")
    assert r.path is None and r.error.startswith("recorder not started") and r.frames == 0
    assert r.effective_fps is None and json.dumps(r.as_dict())  # JSON-safe
    assert not list((tmp_path / "rec").glob("*.mp4"))


def test_manual_after_a_shown_result_records_the_reading_it_came_from(tmp_path):
    recordings = {}
    ex = FakeExecutor(recordings)
    post = FakePost(cosmos_ok("Moves a pen."), responses_body(call({
        **OK_ARGS, "status": "unsupported", "target": None, "destination": None,
        "steps": [], "note": "ペンは対応外"})))  # fmt: skip
    source = VideoSource(VIDEOS, post=post, env=ENV, base_url=COSMOS_URL)
    log = RunsLog(tmp_path / "runs.jsonl", source=source, recordings=recordings)
    screen = ApprovalScreen(executor=log.executor(ex), on_log=log, videos=source.ids)
    c = TestClient(create_app(screen, source))
    c.post("/api/select", json={"video_id": "s1-01"})
    c.post("/api/read")
    assert until(lambda: screen.phase is Phase.FAILED)
    reading = screen.cur.run_id
    assert c.post("/api/manual").status_code == 200
    r = cpost(c, "choose", target="red_cube", destination="box_b")
    assert r.status_code == 200 and screen.wait_idle(5)
    (manual,) = read_rows(tmp_path / "runs.jsonl")
    assert manual["operation"] == "手動" and manual["run_id"].startswith("manual-")
    assert manual["reading_run_id"] == reading and manual["video_id"] == "s1-01"
    assert manual["sentence"] == "Moves a pen." and manual["plan"]["status"] == "unsupported"
    assert manual["final"]["target"] == "red_cube" and manual["commands_sent"] == 7
    # a manual run without any reading stays unattributed
    c.post("/api/manual")
    assert cpost(c, "choose", target="blue_cube", destination="box_a").status_code == 200
    assert screen.wait_idle(5)
    bare = read_rows(tmp_path / "runs.jsonl")[-1]
    assert bare["reading_run_id"] is None and bare["video_id"] is None


def test_render_failure_after_the_run_keeps_the_run_record(tmp_path):
    """The idle frame after a run fails: the RunRecord (outcome, commands) still comes
    back and the failure is noted on the recording (Codex review 1)."""
    from so101_demo.execution.runner import approve_from_review
    from so101_demo.ui.frames import FrameStore
    from so101_demo.ui.recording import ScreenRecorder

    for record_dir in (None, tmp_path / "rec"):
        sr = ScreenRecorder(FrameStore(), record_dir)

        def boom():
            raise RuntimeError("renderer failed")

        sr.render_idle = boom
        runner = SimpleNamespace(
            registry=None,
            submit=lambda a: SimpleNamespace(
                outcome="success", observer_error=None, commands_sent=7
            ),
        )
        rec = sr.run(runner, approve_from_review(source_plan(), "run1"))
        assert rec.outcome == "success" and rec.commands_sent == 7
        assert sr.render_errors == ["RuntimeError: renderer failed"]
        if record_dir is not None:
            assert "idle frame not rendered" in sr.recording_of("run1").error


def test_log_failure_never_breaks_the_operation():
    def boom(entry):
        raise OSError("disk full")

    screen = ApprovalScreen(executor=FakeExecutor(), on_log=boom)
    rid = screen.begin_reading()
    screen.deliver(rid, source_plan(), "s")
    screen.reject(screen.view()["session"])
    assert screen.phase is Phase.IDLE and screen.log_errors and "disk full" in screen.log_errors[0]


def source_plan():
    from so101_demo.plan import validate_plan

    return validate_plan(OK_ARGS)


# --- the wait limit covers the measured understanding time ---------------------------

MEASURED_MAX_S = 33.2  # docs/evidence/uc/eval-r2.md, 2026-09-29: median 19.7 s, max 33.2 s


def test_wait_limit_exceeds_the_measured_understanding_time():
    assert S.RUN_TIMEOUT_S > MEASURED_MAX_S


# --- videos ----------------------------------------------------------------------------


def test_load_videos_from_the_uc_manifest_in_the_given_order():
    vs = load_videos(UC_MANIFEST_PATH, ["uc-10", "uc-02"])
    assert [v.id for v in vs] == ["uc-10", "uc-02"] and all(v.path.is_file() for v in vs)
    assert len(load_videos(UC_MANIFEST_PATH)) == 15
    with pytest.raises(ValueError, match="uc-99"):
        load_videos(UC_MANIFEST_PATH, ["uc-99"])


def test_paths_outside_the_repository_are_recorded_by_name_only(tmp_path):
    from so101_demo.ui.runs_log import repo_path

    assert repo_path(tmp_path / "clips" / "x.mp4") == "x.mp4"
    assert repo_path(VIDEO) == str(VIDEO.relative_to(REPO_ROOT))


def test_ids_without_videos_is_an_error():
    from so101_demo.ui.__main__ import main

    with pytest.raises(SystemExit):
        main(["--ids", "uc-02"])


def test_load_videos_from_a_directory(tmp_path):
    (tmp_path / "b.mp4").write_bytes(b"x")
    (tmp_path / "a.mp4").write_bytes(b"x")
    assert [v.id for v in load_videos(tmp_path)] == ["a", "b"]
    (tmp_path / "empty").mkdir()
    with pytest.raises(ValueError, match="no videos"):
        load_videos(tmp_path / "empty")
    # a manifest may name any file; only mp4 is served as video/mp4 (an absolute path
    # joined to the repository root stays absolute)
    (tmp_path / "c.mov").write_bytes(b"x")
    manifest = tmp_path / "videos.json"
    manifest.write_text(json.dumps({"videos": [{"id": "c", "path": str(tmp_path / "c.mov")}]}))
    with pytest.raises(ValueError, match="not an mp4"):
        load_videos(manifest)


# --- evidence: --check and --report ------------------------------------------------------

COMBOS = evidence_e2e.COMBOS


def row(run_id, video, operation, outcome, plan=None, final=None, mp4=None, commands=0, fps=30.0):
    return {
        "run_id": run_id,
        "at": "2026-10-27T08:00:00.000+00:00",
        "video_id": video,
        "video_path": f"docs/evidence/uc/videos/{video}.mp4",
        "sentence": f"Sentence for {video}.",
        "cosmos": {"failure": None, "finish_reason": "stop", "elapsed_s": 15.0, "model": "c"},
        "plan": plan,
        "astra": {"failure": None, "elapsed_s": 4.0, "model": "gpt-6-astra", "raw_arguments": "{}"},
        "failed_stage": None,
        "source_error": None,
        "operation": operation,
        "rescued": False,
        "shown": plan,
        "final": final,
        "outcome": outcome,
        "reason": None,
        "commands_sent": commands,
        "mp4": mp4,
        "recording": None
        if mp4 is None
        else {
            "path": mp4,
            "bytes": 1_200_000,
            "frames": 300,
            "effective_fps": fps,
            "max_interval_s": 0.3,
            "mean_interval_s": 0.03,
            "sim_span_s": 12.0,
            "encoder": "h264_nvenc",
            "error": None,
        },  # fmt: skip
        "elapsed_s": {"understanding": 19.0, "run": 20.0, "total": 39.0},
        "session": {
            "commit": "abc1234",
            "uncommitted_code": False,
            "cosmos_server_model": S.COSMOS_MODEL,
            "run_timeout_s": 62.0,
            "videos": "contracts/fixtures/uc/videos.json",
            "ids": ["uc-02"],
            "config": {
                "path": "docs/evidence/uc/config-r2.json",
                "sha256": evidence_e2e.sha256_file(evidence_e2e.UC_CONFIG),
                "differences": [],
            },
            "machine": {"gpu": "g", "gl": "gl", "encoder": "h264_nvenc"},
        },
    }


def ok_plan(t, d):
    return {
        "status": "ok", "target": t, "destination": d,
        "steps": [{"action": "pick", "object": t}, {"action": "place", "destination": d}],
        "note": "n",
    }  # fmt: skip


IN_SCOPE_IDS = ("uc-02", "uc-05", "uc-08", "uc-10")  # one manifest video per combination


def complete_rows(base: Path):
    rows = []
    for i, (t, d) in enumerate(COMBOS):
        rid = f"run{i}"
        mp4 = f"{rid}-{t}-{d}.mp4"
        (base / mp4).write_bytes(b"\x00" * 1000)
        plan = ok_plan(t, d)
        rows.append(row(rid, IN_SCOPE_IDS[i], "承認", "success", plan, plan, mp4, 40))
    uns = {"status": "unsupported", "target": None, "destination": None, "steps": [], "note": "x"}
    rows.append(row("run9", "uc-13", "拒否", "not run", uns))
    return rows


def good_probe(path):
    return {"codec": "h264", "width": 1920, "height": 1080, "fps": 25.0, "frames": 300}


def test_check_passes_on_a_complete_record(tmp_path):
    rows = complete_rows(tmp_path)
    assert evidence_e2e.check(rows, tmp_path, probe=good_probe) == []


def test_check_reports_every_missing_piece(tmp_path):
    rows = complete_rows(tmp_path)

    def problems(mutate, probe=good_probe):
        rs = json.loads(json.dumps(rows))
        mutate(rs)
        return evidence_e2e.check(rs, tmp_path, probe=probe)

    assert any("blue_cube -> box_b" in p for p in problems(lambda rs: rs.pop(3)))
    assert any("no out-of-scope video" in p for p in problems(lambda rs: rs.pop(4)))
    # 対応外 is the manifest's classification, not the model's status: rejecting an
    # in-scope video (Codex review 1) does not count, an unknown video is reported
    in_scope = lambda rs: rs[4].update(video_id="uc-02", plan=ok_plan("red_cube", "box_a"))  # noqa: E731
    assert any("no out-of-scope video" in p for p in problems(in_scope))
    assert any("not in videos.json" in p for p in problems(lambda rs: rs[0].update(video_id="x")))
    assert any("commands_sent = 3" in p for p in problems(lambda rs: rs[4].update(commands_sent=3)))
    assert any("missing keys" in p for p in problems(lambda rs: rs[0].pop("sentence")))
    assert any("video missing" in p for p in problems(lambda rs: rs[0].update(mp4="gone.mp4")))
    assert any("but no video" in p for p in problems(lambda rs: rs[1].update(mp4=None)))
    wide = lambda p: {**good_probe(p), "width": 1280}  # noqa: E731
    fast = lambda p: {**good_probe(p), "fps": 30.0}  # noqa: E731
    assert any("width 1280" in p for p in problems(lambda rs: None, probe=wide))
    assert any("fps 30.0" in p for p in problems(lambda rs: None, probe=fast))
    # a run approved after 訂正 does not count as the model's plan for that combination
    corrected = lambda rs: rs[0].update(operation="訂正")  # noqa: E731
    assert any("red_cube -> box_a" in p for p in problems(corrected))
    big = tmp_path / rows[0]["mp4"]
    with big.open("wb") as f:
        f.truncate(10 * 1024 * 1024)
    assert any(">= 10 MiB" in p for p in evidence_e2e.check(rows, tmp_path, probe=good_probe))
    assert evidence_e2e.check([], tmp_path) == ["no rows"]
    # one session on committed code with the fixed configuration and the expected model
    assert any(
        "several sessions" in p for p in problems(lambda rs: rs[1]["session"].update(commit="zzz"))
    )
    assert any(
        "uncommitted" in p
        for p in problems(lambda rs: [r["session"].update(uncommitted_code=True) for r in rs])
    )
    assert any(
        "differed" in p
        for p in problems(
            lambda rs: [r["session"]["config"].update(differences=["cosmos.prompt"]) for r in rs]
        )
    )
    assert any(
        "not the current" in p
        for p in problems(lambda rs: [r["session"]["config"].update(sha256="0" * 64) for r in rs])
    )
    assert any(
        "server model" in p
        for p in problems(lambda rs: [r["session"].update(cosmos_server_model="other") for r in rs])
    )
    # every row is checked, not only the first (Codex review 1)
    second = lambda rs: rs[1]["session"].update(uncommitted_code=True, cosmos_server_model="w")  # noqa: E731
    assert [p for p in problems(second) if p.startswith("line 2")]
    differs = lambda rs: rs[1]["session"]["config"].update(differences=["cosmos.prompt"])  # noqa: E731
    assert any("line 2" in p for p in problems(differs))


def test_report_is_reproducible_conclusion_first_and_names_the_facts(tmp_path):
    rows = complete_rows(tmp_path)
    text = evidence_e2e.report(rows)
    assert text == evidence_e2e.report(json.loads(json.dumps(rows)))
    assert text.startswith("# ") and text.index("## 結論") < text.index("## 条件")
    assert "**4/4**" in text and "**1 本**" in text and "25 fps を割った run なし" in text
    assert "gpt-6-astra" in text and "abc1234" in text and "2026-10-27" in text
    assert "Sentence for uc-13." in text and "現在のファイルと**一致**" in text
    assert "62.0 秒" in text


def test_report_flags_an_ok_on_the_out_of_scope_video_and_a_slow_recording(tmp_path):
    rows = complete_rows(tmp_path)
    rows[4]["plan"] = rows[4]["shown"] = ok_plan("blue_cube", "box_b")
    rows[0]["recording"]["effective_fps"] = 18.0
    text = evidence_e2e.report(rows)
    assert "`ok` を返した" in text and "25 fps を割った run: run0" in text


def test_report_survives_a_one_frame_recording_and_an_in_scope_rejection(tmp_path):
    rows = complete_rows(tmp_path)
    rows[1]["recording"].update(effective_fps=None, max_interval_s=None, frames=1)
    uns = {"status": "unsupported", "target": None, "destination": None, "steps": [], "note": "x"}
    rows.append(row("run8", "uc-02", "拒否", "not run", uns))
    text = evidence_e2e.report(rows)
    assert "fps 計測不能（2 フレーム未満）: run1" in text
    assert (
        "**1 本**" in text and "対応内の動画を「対応外にする」で止めた run: uc-02（`run8`）" in text
    )
    assert text == evidence_e2e.report(json.loads(json.dumps(rows)))


def test_config_differences_is_empty_for_the_fixed_uc_configuration():
    # the fixed acceptance: the Cosmos / Astra instructions are those of Uc revision 2
    assert evidence_e2e.config_differences() == []


def test_config_differences_names_what_changed(tmp_path):
    cfg = json.loads(evidence_e2e.UC_CONFIG.read_text(encoding="utf-8"))
    cfg["cosmos"]["prompt"] = "other"
    cfg["astra"]["timeout_s"] = 1
    p = tmp_path / "c.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    assert evidence_e2e.config_differences(p) == ["cosmos.prompt", "astra.timeout_s"]


def test_main_check_and_report(tmp_path, capsys):
    rows = complete_rows(tmp_path)
    path = tmp_path / "runs.jsonl"
    write = lambda rs: path.write_text(  # noqa: E731
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rs), encoding="utf-8"
    )
    write(rows)
    assert evidence_e2e.main(["--report", str(path)]) == 0
    assert capsys.readouterr().out == evidence_e2e.report(rows)
    rows.pop()
    write(rows)
    assert evidence_e2e.main(["--check", str(path)]) == 1
    assert "no out-of-scope video" in capsys.readouterr().out


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_probe_mp4_reads_size_and_frame_rate(tmp_path):
    out = tmp_path / "t.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=1920x1080:r=25",
         "-frames:v", "3", "-pix_fmt", "yuv420p", str(out)],
        check=True,
    )  # fmt: skip
    fmt = evidence_e2e.probe_mp4(out)
    assert (fmt["width"], fmt["height"], fmt["fps"], fmt["frames"]) == (1920, 1080, 25.0, 3)
    plan = ok_plan("red_cube", "box_a")
    r = row("r", "uc-02", "承認", "success", plan, plan, "t.mp4", 1)
    assert not [p for p in evidence_e2e.check([r], tmp_path) if "t.mp4" in p]


# --- (d) the real big.js switches <video> and <img> on big.kind (node harness) --------

NODE = shutil.which("node")
BIG_HARNESS = Path(__file__).parent / "js" / "big_screen.js"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_big_js_switches_between_the_video_and_the_sim(tmp_path):
    from so101_demo.ui.server import STATIC

    source = VideoSource(TWO, understand=lambda *a, **k: None)
    screen = ApprovalScreen(executor=FakeExecutor(), videos=source.ids)
    views = [screen.view()]  # 待機, nothing selected: sim
    screen.select("uc-02")
    views.append(screen.view())  # 待機, selected: video
    rid = screen.begin_reading()
    views.append(screen.view())  # 読み取り中: video
    screen.deliver(rid, source_plan(), "s")
    views.append(screen.view())  # 承認待ち: video
    screen.cur.final = screen.cur.plan  # 実行中 as the screen shows it, without a runner
    screen._set(Phase.RUNNING)
    views.append(screen.view())  # 実行中: sim
    screen.cur = type(screen.cur)()
    screen._set(Phase.IDLE)
    views.append(screen.view())  # 待機 after the run, still selected: video
    assert [v["big"]["kind"] for v in views] == ["sim", "video", "video", "video", "sim", "video"]
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps(views, ensure_ascii=False), encoding="utf-8")
    out = subprocess.run(
        [NODE, str(BIG_HARNESS), str(STATIC / "app.js"), str(STATIC / "big.js"), str(path)],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    ).stdout
    shown = [json.loads(line) for line in out.splitlines()]
    assert [(s["video_shown"], s["sim_shown"]) for s in shown] == [
        (False, True),
        (True, False),
        (True, False),
        (True, False),
        (False, True),
        (True, False),
    ]
    assert shown[1]["src"] == "/videos/uc-02" and shown[1]["played"] == 1
    assert shown[3]["src"] == "/videos/uc-02"  # the same clip keeps playing, not reloaded
    assert shown[4]["paused"] == 1 and shown[5]["played"] > shown[3]["played"]
    assert shown[4]["phase"] == "実行中"
    # the clip is not rewound while it stays on screen; back from the sim it restarts
    assert shown[2]["time"] == 3 and shown[3]["time"] == 3 and shown[5]["time"] == 0
