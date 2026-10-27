"""Video -> Cosmos sentence -> Astra plan, with the stage that failed.

A Cosmos failure stops the chain: Astra is not called with a truncated or empty sentence,
and the result is an error plan. The returned plan is always a valid contract plan.

Every call has a run ID (`understanding.runs`). With a `RunGate`, the plan goes through
`gate.settle` before it is returned: a result whose run was cancelled, replaced or expired
while the requests were in flight comes back as an error plan with `failed_stage="run"`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from ..plan import Plan
from . import astra, cosmos
from .runs import RunGate, new_run_id


@dataclass(frozen=True)
class Understanding:
    plan: Plan
    failed_stage: str | None  # None, "cosmos", "astra" or "run"
    cosmos: cosmos.CosmosResult
    astra: astra.AstraResult | None
    elapsed_s: float  # video in -> validated final JSON
    run_id: str = ""
    stale: str | None = None  # why the gate dropped the result (failed_stage "run")


def understand(
    video: Path, *, gate: RunGate | None = None, run_id: str | None = None, **kw
) -> Understanding:
    """`run_id` is the gate's run (from `gate.start()`, so the caller can cancel it while
    this blocks); with a gate and no `run_id` a new run is started here. Without a gate
    the call still gets a fresh run ID for the record. `kw` goes to both clients'
    injectable arguments (tests pass fakes)."""
    if run_id is None:
        run_id = gate.start() if gate is not None else new_run_id()
    t0 = time.monotonic()
    c = cosmos.describe(video, **{k: v for k, v in kw.items() if k in ("base_url", "post")})
    if not c.ok:
        plan, stage, a = astra.error_plan("動画の一文を取れなかった"), "cosmos", None
    else:
        a = astra.structure(c.sentence, **{k: v for k, v in kw.items() if k in ("env", "post")})
        plan, stage = a.plan, None if a.ok else "astra"
    stale = None
    if gate is not None:
        settled = gate.settle(run_id, plan)
        if settled.stale is not None:
            plan, stage, stale = settled.plan, "run", settled.stale
    return Understanding(plan, stage, c, a, time.monotonic() - t0, run_id, stale)
