"""The runner on one dedicated thread.

MuJoCo data and an offscreen GL context belong to the thread that uses them: the runner
is built on this thread (its renderer observer creates the GL context there) and every
`submit` runs there too. The screen's worker only waits for the result. `run(fn)` executes
any callable with the runner on that thread (the screen recorder wraps `submit` with it).
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
        self._jobs: queue.Queue[tuple[Callable[[PlanRunner], Any], Future]] = queue.Queue()
        ready: Future[PlanRunner] = Future()

        def loop() -> None:
            try:
                runner = build()
            except BaseException as e:  # noqa: BLE001 - handed to the constructor
                ready.set_exception(e)
                return
            ready.set_result(runner)
            while True:
                fn, done = self._jobs.get()
                try:
                    done.set_result(fn(runner))
                except BaseException as e:  # noqa: BLE001 - handed to the caller
                    done.set_exception(e)

        threading.Thread(target=loop, name="runner", daemon=True).start()
        self.runner = ready.result()

    def run(self, fn: Callable[[PlanRunner], Any]) -> Any:
        """Run `fn(runner)` on the runner thread and return its result."""
        done: Future = Future()
        self._jobs.put((fn, done))
        return done.result()

    def submit(self, approved: ApprovedPlan) -> Any:
        return self.run(lambda runner: runner.submit(approved))
