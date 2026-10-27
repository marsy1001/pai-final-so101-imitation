"""Where the screen's understanding results come from.

`FixedSource` replays the development fixed JSONs of 4a (`contracts/fixtures/4a/`) as if
they were understanding results, after a short delay, so the screen can be driven without
a camera, Cosmos or Astra (the first half of 3b: 最初は固定 JSON). The real chain
(`understanding.pipeline.understand`) plugs in with the same `start(run_id, deliver)`.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from pathlib import Path

from ..plan import Plan, validate_plan
from ..plan.paths import REPO_ROOT

FIXTURES_4A = REPO_ROOT / "contracts" / "fixtures" / "4a"

Deliver = Callable[[str, Plan, str | None], bool]


def load_fixtures(directory: Path = FIXTURES_4A) -> list[tuple[str, Plan]]:
    """(fixture id, plan) for every fixed JSON, sorted by file name."""
    out = []
    for path in sorted(directory.glob("*.json")):
        out.append((path.stem, validate_plan(json.loads(path.read_text(encoding="utf-8")))))
    return out


class FixedSource:
    """Delivers the fixtures in turn, `delay_s` after each start."""

    def __init__(self, plans: list[tuple[str, Plan]] | None = None, delay_s: float = 1.0):
        self.plans = plans if plans is not None else load_fixtures()
        self.delay_s = delay_s
        self._next = 0

    def start(self, run_id: str, deliver: Deliver) -> None:
        fixture_id, plan = self.plans[self._next % len(self.plans)]
        self._next += 1
        sentence = f"（開発用の固定 JSON {fixture_id.split('-')[0]}）"

        def later() -> None:
            deliver(run_id, plan, sentence)

        t = threading.Timer(self.delay_s, later)
        t.daemon = True
        t.start()
