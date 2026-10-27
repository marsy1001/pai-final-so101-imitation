"""Run IDs of the understanding layer: a result that arrives late is dropped.

Risk R1: a late answer expires by run ID. Every understanding call gets a run ID
from `RunGate.start`; the result carries the same ID and goes through `RunGate.settle`
before anything can approve it. Only the gate's current run, settled within its deadline,
keeps its plan. A result is stale, and becomes an error plan (never executable, so
`execution.runner.ApprovedPlan` refuses it), when its run

- was cancelled (`cancel`: the operator gave up, or the screen moved on),
- was replaced (`start` was called again: the next request supersedes this one),
- expired (it arrived more than `timeout_s` after `start`), or
- was already settled (a second result for one run is not a second chance).

After settling, `claim(run_id, plan)` is the last check before
`approve_from_review(plan, run_id)`: the run ID the approval records is the gate's run ID,
and a run cancelled or replaced while the plan waited on screen can no longer be approved.
There is no retry here (project rule: no retry optimisation).
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from ..plan import Plan
from . import settings as S
from .astra import error_plan

CANCELLED = "cancelled"
SUPERSEDED = "superseded"
EXPIRED = "expired"
UNKNOWN = "unknown"
SETTLED = "already settled"


class StaleRun(Exception):
    """A result or an approval for a run that is no longer the current one."""


@dataclass(frozen=True)
class Settled:
    run_id: str
    plan: Plan  # the result's plan when current, an error plan when stale
    stale: str | None  # None, or why the result was dropped
    arrived_s: float  # seconds from start to settle


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]


class RunGate:
    """The current run of one screen. Thread-safe: the request runs in a worker, cancel
    and replace come from the screen."""

    def __init__(
        self,
        timeout_s: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.timeout_s = S.RUN_TIMEOUT_S if timeout_s is None else timeout_s
        self._clock = clock
        self._lock = threading.Lock()
        self._current: str | None = None
        self._started: dict[str, float] = {}
        self._ended: dict[str, str] = {}  # run id -> CANCELLED / SUPERSEDED / SETTLED
        self._executable: dict[str, Plan] = {}  # settled current run -> its plan

    @property
    def current(self) -> str | None:
        with self._lock:
            return self._current

    def start(self) -> str:
        """A new run; the previous one, if any, is superseded."""
        run_id = new_run_id()
        with self._lock:
            if self._current is not None:
                self._end(self._current, SUPERSEDED)
            self._current = run_id
            self._started[run_id] = self._clock()
        return run_id

    def cancel(self, run_id: str | None = None) -> None:
        """Cancel `run_id`, or the current run when None. Cancelling an old run is a no-op."""
        with self._lock:
            target = self._current if run_id is None else run_id
            if target is not None and target == self._current:
                self._end(target, CANCELLED)
                self._current = None

    def _end(self, run_id: str, why: str) -> None:
        self._ended[run_id] = why
        self._executable.pop(run_id, None)

    def _stale(self, run_id: str, now: float) -> str | None:
        if run_id not in self._started:
            return UNKNOWN
        if run_id in self._ended:
            return self._ended[run_id]
        if now - self._started[run_id] > self.timeout_s:
            return EXPIRED
        return None

    def settle(self, run_id: str, plan: Plan) -> Settled:
        """Accept `plan` as the result of `run_id`, or replace it with an error plan."""
        with self._lock:
            now = self._clock()
            arrived = now - self._started.get(run_id, now)
            stale = self._stale(run_id, now)
            if stale is None:
                self._ended[run_id] = SETTLED
                if plan.executable:
                    self._executable[run_id] = plan
                return Settled(run_id, plan, None, arrived)
        return Settled(run_id, error_plan(f"遅れて届いた結果を破棄: {stale}"), stale, arrived)

    def claim(self, run_id: str, plan: Plan) -> tuple[Plan, str]:
        """(plan, run_id) for `approve_from_review`, only when `plan` is the settled,
        executable result of the current run. Raises StaleRun otherwise; a run is claimed
        at most once."""
        with self._lock:
            kept = self._executable.get(run_id)
            if kept is None or kept != plan:
                why = self._ended.get(run_id, UNKNOWN) if run_id in self._started else UNKNOWN
                if why == SETTLED:
                    why = "not the settled executable plan, or already claimed"
                raise StaleRun(f"run {run_id} cannot be approved ({why})")
            del self._executable[run_id]
            return kept, run_id
