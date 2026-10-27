"""Approval screen logic (the approval screen, 3b).

One `ApprovalScreen` owns what the audience and the operator see and the only way from an
understanding result to the runner. It holds no HTTP and no rendering: the server
(`ui.server`) calls these methods and shows `view()`.

Phases (the labels are what the screens show):

    待機 --read--> 読み取り中 --result ok--> 承認待ち --approve / correct--> 実行中 --> 待機
                             --result not ok / over the wait limit--> 拒否 / 失敗
    承認待ち, 拒否 / 失敗 --reject--> 待機            (対応外拒否: nothing moves)
    any phase but 実行中 --manual--> 手動 --choose--> 実行中
    待機 --prerecorded--> 事前録画 --idle--> 待機     (display only; playback is not here)

The four paths of the plan: 承認 (the model's plan as shown), 訂正 (the operator changes
the target or the destination of a shown result), 拒否 (対応外: nothing runs) and 手動
(no usable model output: pick one of the registered pairs). Every plan that reaches the
runner is re-validated against the contract and approved with `approve_from_review`, the
only review approval there is; the model's own plan also passes `RunGate.claim` first.

A result is delivered with its run ID and settled through the `RunGate`: a result
whose run was cancelled (manual, reject, a new reading) or replaced, or that arrived after
the wait limit, is dropped and changes nothing on screen. Rescued runs (訂正, 手動) are
logged as such and never count as automatic understanding (the project rule).

With real videos (`videos`) the operator first selects one (`select`, only in
待機); 読み取り開始 sends that video. The big screen follows `view()["big"]`: the selected
person's video (kind "video") while it is selected, read and shown or refused, the sim
(kind "sim") while the robot runs, in 手動 / 事前録画 and when nothing is selected.
"""

from __future__ import annotations

import datetime as dt
import threading
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from ..execution.runner import ApprovedPlan, approve_from_review
from ..execution.runner import _plan_dict as runner_plan_dict
from ..plan import Plan, PlanInvalid, Registry, load_registry, validate_plan
from ..understanding.runs import RunGate, StaleRun


class Phase(StrEnum):
    IDLE = "待機"
    READING = "読み取り中"
    RESULT = "承認待ち"
    FAILED = "拒否 / 失敗"
    MANUAL = "手動"
    RUNNING = "実行中"
    PRERECORDED = "事前録画"


APPROVE = "承認"
CORRECT = "訂正"
REJECT = "拒否"
MANUAL = "手動"
SELECT = "選択"
RESCUE_PATHS = (CORRECT, MANUAL)

# What the screen calls the registered ids. Colours come from the registry (rgba).
COLOR_WORDS = {"red": "赤い", "blue": "青い", "gray": "灰色の", "yellow": "黄色の"}
REASON_TEXT = dict(
    unsupported="対応外の作業と判断しました",
    needs_review="対象か置き先がはっきりしません",
    error="読み取りに失敗しました",
)


class ActionRefused(Exception):
    """An operation that is not allowed in the current phase; nothing changed."""


Executor = Callable[[ApprovedPlan], Any]  # returns a RunRecord (or anything with .outcome)


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="milliseconds")


def _plan_dict(plan: Plan | None) -> dict | None:
    return None if plan is None else runner_plan_dict(plan)


@dataclass
class LogEntry:
    at: str
    run_id: str | None
    path: str  # 承認 / 訂正 / 拒否 / 手動, or the refused / dropped action
    shown: dict | None  # the model's plan on screen when the operator acted
    final: dict | None  # the plan sent to the runner (None when nothing ran)
    result: str  # runner outcome, "not run", "refused: ...", "dropped: ..."
    rescued: bool  # 訂正 / 手動: never counted as automatic understanding
    reading_run_id: str | None = None  # 手動 after a shown result: the reading's run id

    def as_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class _Current:
    run_id: str | None = None
    sentence: str | None = None
    plan: Plan | None = None  # settled model plan (RESULT / FAILED)
    final: Plan | None = None  # the plan being run (RUNNING); differs after 訂正 / 手動
    reason: str | None = None  # why FAILED
    started_at: float | None = None
    video: str | None = None  # what the source is reading (a video id); shown on screen
    reading_run_id: str | None = None  # in 手動: the settled reading that was on screen


@dataclass
class ApprovalScreen:
    executor: Executor
    gate: RunGate = field(default_factory=RunGate)
    registry: Registry = field(default_factory=load_registry)
    clock: Callable[[], float] = time.monotonic  # the wait limit; pass the gate's clock
    on_log: Callable[[LogEntry], None] | None = None  # every log entry (the run log)
    videos: Sequence[str] = ()  # ids the operator can select (`--videos`); empty: fixed JSONs

    def __post_init__(self) -> None:
        self._lock = threading.RLock()
        self.phase = Phase.IDLE
        self.cur = _Current()
        self.log: list[LogEntry] = []
        self.last_outcome: str | None = None
        self.version = 0  # grows with every change; pages never draw an older view
        self.boot = uuid.uuid4().hex[:8]  # tells pages that the server restarted
        self.videos = list(self.videos)
        self.selected: str | None = None  # the video 読み取り開始 will send (kept after a run)
        self._worker: threading.Thread | None = None
        self._idle = threading.Event()
        self._idle.set()
        self.log_errors: list[str] = []  # `on_log` failures; never break an operation

    # --- helpers -------------------------------------------------------------------
    def _set(self, phase: Phase) -> None:
        self.phase = phase
        self.version += 1

    def _log(
        self,
        path: str,
        final: Plan | None,
        result: str,
        *,
        run_id: str | None = None,
        shown: Plan | None = None,
        reading_run_id: str | None = None,
    ) -> None:
        entry = LogEntry(
            _now(),
            run_id or self.cur.run_id,
            path,
            _plan_dict(shown or self.cur.plan),
            _plan_dict(final),
            result,
            path in RESCUE_PATHS,
            reading_run_id if reading_run_id is not None else self.cur.reading_run_id,
        )
        self.log.append(entry)
        self.version += 1
        if self.on_log is not None:
            try:
                self.on_log(entry)
            except Exception as e:  # noqa: BLE001 - the log is outside the plan path
                self.log_errors.append(f"{type(e).__name__}: {e}")

    def _refuse(self, action: str, why: str) -> None:
        self._log(action, None, f"refused: {why}")
        raise ActionRefused(f"{action}: {why}")

    def _expire_if_over(self) -> None:
        if self.phase is Phase.READING and self.cur.started_at is not None:
            if self.clock() - self.cur.started_at > self.gate.timeout_s:
                self.gate.cancel(self.cur.run_id)
                self.cur.reason = "待機上限を超えました"
                self._log("読み取り", None, "dropped: over the wait limit")
                self._set(Phase.FAILED)

    def plan_for(self, target: str, destination: str, note: str) -> Plan:
        """A contract plan for one registered pair; PlanInvalid for anything else."""
        return validate_plan(
            {
                "status": "ok",
                "target": target,
                "destination": destination,
                "steps": [
                    {"action": "pick", "object": target},
                    {"action": "place", "destination": destination},
                ],
                "note": note,
            }
        )

    # --- selecting and reading -----------------------------------------------------
    def select(self, video_id: str | None) -> None:
        """Pick the video 読み取り開始 sends (and the big screen shows): a registered id,
        only in 待機."""
        with self._lock:
            self._expire_if_over()
            if self.phase is not Phase.IDLE:
                self._refuse(SELECT, f"phase is {self.phase.value}")
            if video_id not in self.videos:
                self._refuse(SELECT, f"unknown video {video_id!r}")
            self.selected = video_id
            self.version += 1

    def begin_reading(self) -> str:
        """Start a run; with registered videos one must be selected (`selected` is what
        the source reads, and `cur.video` keeps it on screen until 待機)."""
        with self._lock:
            self._expire_if_over()
            if self.phase is not Phase.IDLE:
                self._refuse("読み取り", f"phase is {self.phase.value}")
            if self.videos and self.selected is None:
                self._refuse("読み取り", "no video selected")
            run_id = self.gate.start()
            self.cur = _Current(run_id=run_id, started_at=self.clock(), video=self.selected)
            self._set(Phase.READING)
            return run_id

    def label_reading(self, run_id: str, label: str | None) -> None:
        """What the source started reading for `run_id` (a video id), for the screens."""
        with self._lock:
            if label and run_id == self.cur.run_id:
                self.cur.video = label
                self.version += 1

    def abort_reading(self, run_id: str, why: str) -> None:
        """The source could not start: close the run and go back to 待機."""
        with self._lock:
            if self.phase is Phase.READING and run_id == self.cur.run_id:
                self.gate.cancel(run_id)
                self._log("読み取り", None, f"dropped: source failed: {why}")
                self.cur = _Current()
                self._set(Phase.IDLE)

    def deliver(self, run_id: str, plan: Plan, sentence: str | None = None) -> bool:
        """An understanding result for `run_id`. False when it was dropped (stale)."""
        with self._lock:
            self._expire_if_over()
            settled = self.gate.settle(run_id, plan)
            if settled.stale is not None or run_id != self.cur.run_id:
                why = settled.stale or "not the run on screen"
                self._log("読み取り", None, f"dropped: {why}", run_id=run_id, shown=plan)
                return False
            self.cur.sentence = sentence
            self.cur.plan = settled.plan
            if settled.plan.executable:
                self._set(Phase.RESULT)
            else:
                self.cur.reason = REASON_TEXT.get(settled.plan.status, settled.plan.status)
                self._set(Phase.FAILED)
            return True

    # --- the four paths ------------------------------------------------------------
    def _check_session(self, action: str, session: str | None) -> None:
        """The operation must name the run the operator was looking at (`view()["session"]`):
        a stale page or a late request cannot act on whatever is on screen now."""
        if not session or session != self.cur.run_id:
            self._refuse(action, f"session {session!r} is not the one on screen")

    def approve(self, session: str | None) -> None:
        with self._lock:
            self._expire_if_over()
            if self.phase is not Phase.RESULT or self.cur.plan is None:
                self._refuse(APPROVE, f"phase is {self.phase.value}")
            self._check_session(APPROVE, session)
            try:
                plan, run_id = self.gate.claim(self.cur.run_id, self.cur.plan)
            except StaleRun as e:
                self._refuse(APPROVE, str(e))
            self._run(APPROVE, approve_from_review(plan, run_id))

    def correct(self, session: str | None, target: str, destination: str) -> None:
        with self._lock:
            self._expire_if_over()
            if self.phase not in (Phase.RESULT, Phase.FAILED) or self.cur.plan is None:
                self._refuse(CORRECT, f"phase is {self.phase.value}")
            self._check_session(CORRECT, session)
            try:
                plan = self.plan_for(target, destination, "承認画面で訂正")
            except PlanInvalid as e:
                self._refuse(CORRECT, f"invalid: {e}")
            self.gate.cancel(self.cur.run_id)  # nothing else from this run can be approved
            self._run(CORRECT, approve_from_review(plan, self.cur.run_id))

    def reject(self, session: str | None) -> None:
        with self._lock:
            self._expire_if_over()
            if self.phase not in (Phase.RESULT, Phase.FAILED):
                self._refuse(REJECT, f"phase is {self.phase.value}")
            self._check_session(REJECT, session)
            self.gate.cancel(self.cur.run_id)
            self._log(REJECT, None, "not run")
            self.cur = _Current()
            self._set(Phase.IDLE)

    def enter_manual(self) -> None:
        with self._lock:
            if self.phase is Phase.RUNNING:
                self._refuse(MANUAL, "phase is 実行中")
            self.gate.cancel()  # a result still on its way is dropped when it arrives
            # a manual session of its own; the model's plan stays for the log only, and
            # the log keeps which reading it was (a result on screen: RESULT / FAILED)
            if self.cur.plan is not None and self.cur.reading_run_id is None:
                self.cur.reading_run_id = self.cur.run_id
            self.cur.run_id = f"manual-{uuid.uuid4().hex[:8]}"
            self._set(Phase.MANUAL)

    def choose(self, session: str | None, target: str, destination: str) -> None:
        with self._lock:
            if self.phase is not Phase.MANUAL:
                self._refuse(MANUAL, f"phase is {self.phase.value}")
            self._check_session(MANUAL, session)
            try:
                plan = self.plan_for(target, destination, "手動選択")
            except PlanInvalid as e:
                self._refuse(MANUAL, f"invalid: {e}")
            self._run(MANUAL, approve_from_review(plan, self.cur.run_id))

    def prerecorded(self) -> None:
        with self._lock:
            if self.phase is Phase.RUNNING:
                self._refuse("事前録画", "phase is 実行中")
            self.gate.cancel()
            self._set(Phase.PRERECORDED)

    def back_to_idle(self) -> None:
        with self._lock:
            if self.phase is Phase.RUNNING:
                self._refuse("待機", "phase is 実行中")
            self.gate.cancel()
            self.cur = _Current()
            self._set(Phase.IDLE)

    # --- execution -----------------------------------------------------------------
    def _run(self, path: str, approved: ApprovedPlan) -> None:
        self.cur.final = approved.plan  # what the screens show while it runs
        self._set(Phase.RUNNING)
        self._idle.clear()
        run_id, shown, reading = self.cur.run_id, self.cur.plan, self.cur.reading_run_id

        def work() -> None:
            try:
                rec = self.executor(approved)
                outcome = str(getattr(rec, "outcome", rec))
            except Exception as e:  # noqa: BLE001 - recorded; the screen must return to 待機
                outcome = f"error: {type(e).__name__}: {e}"
            with self._lock:
                try:
                    self._log(
                        path,
                        approved.plan,
                        outcome,
                        run_id=run_id,
                        shown=shown,
                        reading_run_id=reading,
                    )
                finally:  # the screen returns to 待機 even if the log fails
                    self.last_outcome = outcome
                    self.cur = _Current()
                    self._set(Phase.IDLE)
                    self._idle.set()

        self._worker = threading.Thread(target=work, name=f"run-{path}", daemon=True)
        self._worker.start()

    def wait_idle(self, timeout: float | None = None) -> bool:
        return self._idle.wait(timeout)

    # --- what the screens show -----------------------------------------------------
    def _object(self, oid: str | None) -> dict | None:
        o = self.registry.objects.get(oid) if oid else None
        if o is None:
            return None
        return {
            "id": o.id,
            "name": f"{COLOR_WORDS.get(o.color, '')}キューブ",
            "label": o.label,
            "text": f"{COLOR_WORDS.get(o.color, '')}キューブ {o.label}",
            "css": _css(o.rgba),
        }

    def _destination(self, did: str | None) -> dict | None:
        d = self.registry.destinations.get(did) if did else None
        if d is None:
            return None
        return {
            "id": d.id,
            "name": "箱",
            "label": d.label,
            "text": f"箱 {d.label}",
            "css": _css(d.rgba),
        }

    def candidates(self) -> list[dict]:
        return [
            {"target": self._object(o), "destination": self._destination(d)}
            for o in self.registry.objects
            for d in self.registry.destinations
        ]

    def actions(self) -> list[str]:
        p = self.phase
        return {
            Phase.IDLE: (["select"] if self.videos else []) + ["read", "manual", "prerecorded"],
            Phase.READING: ["manual", "prerecorded"],
            Phase.RESULT: ["approve", "correct", "reject", "manual"],
            # 訂正 needs a result on screen; after the wait limit there is none
            Phase.FAILED: (["correct"] if self.cur.plan else []) + ["reject", "manual", "idle"],
            Phase.MANUAL: ["choose", "idle", "prerecorded"],
            Phase.RUNNING: [],
            Phase.PRERECORDED: ["idle"],
        }[p]

    def big(self) -> dict:
        """What the big screen shows: the person's video while it is selected (待機), read,
        shown or refused; the sim while the robot runs and whenever no video is on."""
        # only registered videos: a fixed JSON's label (`label_reading`) is not a video
        on = self.cur.video if self.phase in (Phase.READING, Phase.RESULT, Phase.FAILED) else None
        on = self.selected if self.phase is Phase.IDLE else on
        if on in self.videos:
            return {"kind": "video", "video": on}
        return {"kind": "sim", "video": None}

    def view(self) -> dict:
        """Everything the two screens show. No raw plan, no model or API names."""
        with self._lock:
            self._expire_if_over()
            plan = self.cur.final if self.phase is Phase.RUNNING else self.cur.plan
            waiting = None
            if self.phase is Phase.READING and self.cur.started_at is not None:
                waiting = round(self.clock() - self.cur.started_at, 1)
            return {
                "version": self.version,
                "boot": self.boot,
                "phase": self.phase.value,
                "phase_key": self.phase.name.lower(),
                # sent back with approve / correct / reject / choose (see _check_session)
                "session": self.cur.run_id
                if self.phase in (Phase.RESULT, Phase.FAILED, Phase.MANUAL)
                else None,
                # the plan's Japanese note (the contract's one-sentence summary for the
                # screen); the model's English sentence stays off screen
                "reading": self.cur.plan.note
                if self.cur.plan and self.phase in (Phase.RESULT, Phase.FAILED, Phase.MANUAL)
                else None,
                "target": self._object(plan.target) if plan else None,
                "destination": self._destination(plan.destination) if plan else None,
                "message": self.cur.reason,
                "video": self.cur.video,  # the video being / last read, until 待機
                "videos": [{"id": v} for v in self.videos],  # the list to select from
                "selected": self.selected,
                "big": self.big(),
                "waiting_s": waiting,
                "wait_limit_s": self.gate.timeout_s,
                "candidates": self.candidates() if self.phase is Phase.MANUAL else [],
                "objects": [self._object(o) for o in self.registry.objects],
                "destinations": [self._destination(d) for d in self.registry.destinations],
                "actions": self.actions(),
                "last_result": _outcome_text(self.last_outcome),
            }


def _css(rgba) -> str:
    r, g, b = (round(255 * c) for c in rgba[:3])
    return f"#{r:02x}{g:02x}{b:02x}"


def _outcome_text(outcome: str | None) -> str | None:
    if outcome is None:
        return None
    if outcome == "success":
        return "完了しました"
    return "うまくいきませんでした"
