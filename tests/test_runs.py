"""Run IDs of the understanding layer.

Nothing here calls Cosmos or Astra: the pipeline tests use a fake `post`.
"""

import threading

import pytest
from test_understanding import ENV, OK_ARGS, VIDEO, FakePost, call, chat_body, responses_body

from so101_demo.execution.runner import REVIEW_APPROVAL_SOURCE, approve_from_review
from so101_demo.plan import validate_plan
from so101_demo.understanding import pipeline, runs
from so101_demo.understanding import settings as S
from so101_demo.understanding.runs import RunGate, StaleRun

OK = validate_plan(OK_ARGS)


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def test_current_result_within_the_deadline_is_kept_and_claimable():
    gate = RunGate(timeout_s=10.0)
    rid = gate.start()
    s = gate.settle(rid, OK)
    assert s.stale is None and s.plan == OK and s.run_id == rid
    plan, claimed = gate.claim(rid, s.plan)
    approved = approve_from_review(plan, claimed)
    assert approved.reference == rid and approved.source == REVIEW_APPROVAL_SOURCE


@pytest.mark.parametrize(
    ("action", "stale"),
    [
        (lambda g, rid: g.cancel(rid), runs.CANCELLED),
        (lambda g, rid: g.cancel(), runs.CANCELLED),
        (lambda g, rid: g.start(), runs.SUPERSEDED),
    ],
    ids=["cancel-by-id", "cancel-current", "replaced"],
)
def test_result_after_cancel_or_replace_is_dropped(action, stale):
    gate = RunGate(timeout_s=10.0)
    rid = gate.start()
    action(gate, rid)
    s = gate.settle(rid, OK)
    assert s.stale == stale
    assert s.plan.status == "error" and not s.plan.executable
    with pytest.raises(StaleRun):
        gate.claim(rid, OK)
    with pytest.raises(ValueError):  # the dropped result cannot even be approved directly
        approve_from_review(s.plan, rid)


def test_result_after_the_deadline_is_dropped():
    clock = Clock()
    gate = RunGate(timeout_s=5.0, clock=clock)
    rid = gate.start()
    clock.t += 5.01
    s = gate.settle(rid, OK)
    assert s.stale == runs.EXPIRED and not s.plan.executable
    assert s.arrived_s == pytest.approx(5.01)


def test_result_exactly_at_the_deadline_is_kept():
    clock = Clock()
    gate = RunGate(timeout_s=5.0, clock=clock)
    rid = gate.start()
    clock.t += 5.0
    assert gate.settle(rid, OK).stale is None


def test_the_new_run_is_not_affected_by_the_old_result():
    gate = RunGate(timeout_s=10.0)
    old = gate.start()
    new = gate.start()
    assert gate.settle(old, OK).stale == runs.SUPERSEDED
    assert gate.current == new
    s = gate.settle(new, OK)
    assert s.stale is None
    assert gate.claim(new, s.plan) == (OK, new)


def test_cancelling_an_old_run_does_not_cancel_the_current_one():
    gate = RunGate(timeout_s=10.0)
    old = gate.start()
    new = gate.start()
    gate.cancel(old)
    assert gate.current == new and gate.settle(new, OK).stale is None


def test_a_second_result_for_one_run_is_dropped():
    gate = RunGate(timeout_s=10.0)
    rid = gate.start()
    gate.settle(rid, OK)
    assert gate.settle(rid, OK).stale == runs.SETTLED


def test_unknown_run_is_dropped():
    gate = RunGate(timeout_s=10.0)
    gate.start()
    assert gate.settle("not-a-run", OK).stale == runs.UNKNOWN
    with pytest.raises(StaleRun):
        gate.claim("not-a-run", OK)


def test_cancel_while_the_plan_waits_for_approval_blocks_the_claim():
    gate = RunGate(timeout_s=10.0)
    rid = gate.start()
    s = gate.settle(rid, OK)
    gate.cancel()
    with pytest.raises(StaleRun, match=runs.CANCELLED):
        gate.claim(rid, s.plan)


def test_replace_while_the_plan_waits_for_approval_blocks_the_claim():
    gate = RunGate(timeout_s=10.0)
    rid = gate.start()
    s = gate.settle(rid, OK)
    gate.start()
    with pytest.raises(StaleRun, match=runs.SUPERSEDED):
        gate.claim(rid, s.plan)


def test_a_run_is_claimed_once_and_only_for_its_own_plan():
    gate = RunGate(timeout_s=10.0)
    rid = gate.start()
    gate.settle(rid, OK)
    other = validate_plan(
        OK_ARGS
        | {
            "target": "blue_cube",
            "steps": [{"action": "pick", "object": "blue_cube"}, OK_ARGS["steps"][1]],
        }
    )
    with pytest.raises(StaleRun):
        gate.claim(rid, other)
    gate.claim(rid, OK)
    with pytest.raises(StaleRun):
        gate.claim(rid, OK)


def test_a_non_executable_result_is_not_claimable():
    gate = RunGate(timeout_s=10.0)
    rid = gate.start()
    s = gate.settle(rid, validate_plan(OK_ARGS | {"status": "needs_review", "steps": []}))
    assert s.stale is None
    with pytest.raises(StaleRun):
        gate.claim(rid, s.plan)


def test_default_deadline_is_both_request_deadlines_plus_slack():
    assert RunGate().timeout_s == S.RUN_TIMEOUT_S
    assert S.RUN_TIMEOUT_S == S.COSMOS_TIMEOUT_S + S.ASTRA_TIMEOUT_S + S.RUN_SLACK_S
    assert 0 < S.RUN_SLACK_S < 10


def test_run_ids_are_distinct():
    gate = RunGate()
    assert len({gate.start() for _ in range(100)}) == 100


# --- pipeline -------------------------------------------------------------------------


def good_post():
    return FakePost(
        chat_body("A hand puts the red cube into the left tray."), responses_body(call(OK_ARGS))
    )


def test_pipeline_result_carries_the_run_id_it_was_given():
    gate = RunGate(timeout_s=10.0)
    rid = gate.start()
    u = pipeline.understand(VIDEO, gate=gate, run_id=rid, env=ENV, post=good_post())
    assert u.run_id == rid and u.failed_stage is None and u.plan.executable
    assert gate.claim(u.run_id, u.plan) == (u.plan, rid)


def test_pipeline_starts_a_run_when_given_only_a_gate():
    gate = RunGate(timeout_s=10.0)
    u = pipeline.understand(VIDEO, gate=gate, env=ENV, post=good_post())
    assert u.run_id and u.run_id == gate.current


def test_pipeline_without_a_gate_still_has_a_run_id():
    u = pipeline.understand(VIDEO, env=ENV, post=good_post())
    assert u.run_id and u.plan.executable and u.stale is None


class BlockingPost(FakePost):
    """Holds the Astra request until released, so the test can cancel or replace."""

    def __init__(self, *responses):
        super().__init__(*responses)
        self.astra_seen = threading.Event()
        self.release = threading.Event()

    def __call__(self, url, headers, body, timeout):
        if url.endswith("/responses"):
            self.astra_seen.set()
            assert self.release.wait(5.0)
        return super().__call__(url, headers, body, timeout)


@pytest.mark.parametrize(
    ("action", "stale"),
    [(lambda g, rid: g.cancel(rid), runs.CANCELLED), (lambda g, rid: g.start(), runs.SUPERSEDED)],
    ids=["cancelled", "replaced"],
)
def test_pipeline_drops_a_good_result_that_arrives_after_cancel_or_replace(action, stale):
    gate = RunGate(timeout_s=10.0)
    rid = gate.start()
    post = BlockingPost(chat_body("A sentence."), responses_body(call(OK_ARGS)))
    out = {}
    t = threading.Thread(
        target=lambda: out.setdefault(
            "u", pipeline.understand(VIDEO, gate=gate, run_id=rid, env=ENV, post=post)
        )
    )
    t.start()
    assert post.astra_seen.wait(5.0)
    action(gate, rid)
    post.release.set()
    t.join(5.0)
    u = out["u"]
    assert u.astra.ok and u.astra.plan.executable  # Astra itself answered ok
    assert u.run_id == rid and u.failed_stage == "run" and u.stale == stale
    assert u.plan.status == "error" and not u.plan.executable
    with pytest.raises(StaleRun):
        gate.claim(u.run_id, u.astra.plan)


def test_pipeline_drops_a_result_after_the_run_deadline():
    clock = Clock()
    gate = RunGate(timeout_s=1.0, clock=clock)
    rid = gate.start()

    class SlowPost(FakePost):
        def __call__(self, url, headers, body, timeout):
            clock.t += 0.6  # each request takes 0.6 s of the gate's clock
            return super().__call__(url, headers, body, timeout)

    post = SlowPost(chat_body("A sentence."), responses_body(call(OK_ARGS)))
    u = pipeline.understand(VIDEO, gate=gate, run_id=rid, env=ENV, post=post)
    assert u.failed_stage == "run" and u.stale == runs.EXPIRED and not u.plan.executable
