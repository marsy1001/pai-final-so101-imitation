"""The runner on one dedicated thread.

MuJoCo data and an offscreen GL context belong to the thread that uses them: the runner
is built on this thread (its renderer observer creates the GL context there) and every
`submit` runs there too. The screen's worker only waits for the result.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from concurrent.futures import Future
from typing import Any

from ..execution.runner import ApprovedPlan, PlanRunner


class RunnerThread:
    def __init__(self, build: Callable[[], PlanRunner]) -> None:
        self._jobs: queue.Queue[tuple[ApprovedPlan, Future]] = queue.Queue()
        ready: Future[PlanRunner] = Future()

        def loop() -> None:
            try:
                runner = build()
            except BaseException as e:  # noqa: BLE001 - handed to the constructor
                ready.set_exception(e)
                return
            ready.set_result(runner)
            while True:
                approved, done = self._jobs.get()
                try:
                    done.set_result(runner.submit(approved))
                except BaseException as e:  # noqa: BLE001 - handed to the caller
                    done.set_exception(e)

        threading.Thread(target=loop, name="runner", daemon=True).start()
        self.runner = ready.result()

    def submit(self, approved: ApprovedPlan) -> Any:
        done: Future = Future()
        self._jobs.put((approved, done))
        return done.result()
