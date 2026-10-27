"""One line per run in `<record dir>/runs.jsonl` (Issue #3).

A run is one operator decision on one reading: 承認 / 訂正 / 手動 (the runner ran) or
拒否 (nothing ran). Refused operations and dropped results are not runs and are not
written (they stay in `ApprovalScreen.log`). Each line joins three records by run ID:
the reading (`VideoSource.runs`: video, Cosmos sentence, Astra plan, time), the screen's
log entry (operation, shown and final plan, outcome) and the execution (the runner's
`RunRecord`: commands sent, wall time; the `Recording`: mp4, size, frame rate).

`REQUIRED_KEYS` is what `ui.evidence_e2e --check` demands of every line.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ..execution.runner import ApprovedPlan
from ..execution.runner import _plan_dict as runner_plan_dict
from ..plan.paths import REPO_ROOT
from .recording import Recording
from .screen import APPROVE, CORRECT, MANUAL, REJECT, Executor, LogEntry
from .sources import VideoSource

RUN_PATHS = (APPROVE, CORRECT, REJECT, MANUAL)
REQUIRED_KEYS = (
    "run_id",
    "at",
    "video_id",
    "video_path",
    "sentence",
    "plan",
    "operation",
    "outcome",
    "commands_sent",
    "mp4",
    "elapsed_s",
)


def _plan_dict(plan) -> dict | None:
    return None if plan is None else runner_plan_dict(plan)


def repo_path(path: Path) -> str:
    """A path for the record: relative to the repository, or the file name alone for a
    file outside it (the record is committed and the repository becomes public: no home
    directories in it)."""
    path = Path(path)
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return path.name


class RunsLog:
    """`executor(inner)` wraps the screen's executor to keep every `RunRecord`; the
    instance itself is the screen's `on_log` hook and writes the lines."""

    def __init__(
        self,
        path: Path,
        *,
        source: VideoSource | None = None,
        recordings: Mapping[str, Recording] | None = None,
        session: Mapping[str, Any] | Callable[[], Mapping[str, Any]] | None = None,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.source = source
        self.recordings = recordings if recordings is not None else {}
        self._session = session
        self.records: dict[str, Any] = {}  # run id -> RunRecord
        self.rows: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def executor(self, inner: Executor) -> Executor:
        def run(approved: ApprovedPlan):
            rec = inner(approved)
            with self._lock:
                self.records[approved.reference] = rec
            return rec

        return run

    def session(self) -> dict[str, Any]:
        s = self._session() if callable(self._session) else self._session
        return dict(s or {})

    def row(self, entry: LogEntry) -> dict[str, Any]:
        reading_id = entry.reading_run_id or entry.run_id
        reading = self.source.run_of(reading_id) if self.source is not None else None
        u = reading.understanding if reading is not None else None
        with self._lock:
            rec = self.records.get(entry.run_id)
        recording = self.recordings.get(entry.run_id)
        understanding_s = round(u.elapsed_s, 2) if u is not None else None
        run_s = round(float(rec.wall_time_s), 2) if rec is not None else None
        return {
            "run_id": entry.run_id,
            "reading_run_id": reading.run_id if reading is not None else None,
            "at": entry.at,
            "video_id": reading.video.id if reading is not None else None,
            "video_path": repo_path(reading.video.path) if reading is not None else None,
            "sentence": u.cosmos.sentence if u is not None else None,
            "cosmos": None
            if u is None
            else {
                "failure": u.cosmos.failure,
                "finish_reason": u.cosmos.finish_reason,
                "elapsed_s": round(u.cosmos.elapsed_s, 2),
                "model": u.cosmos.request.get("model"),
            },
            "plan": _plan_dict(u.plan) if u is not None else None,
            "astra": None
            if u is None or u.astra is None
            else {
                "failure": u.astra.failure,
                "elapsed_s": round(u.astra.elapsed_s, 2),
                "model": u.astra.model,
                "raw_arguments": u.astra.raw_arguments,
            },
            "failed_stage": u.failed_stage if u is not None else None,
            "source_error": reading.error if reading is not None else None,
            "operation": entry.path,
            "rescued": entry.rescued,
            "shown": entry.shown,
            "final": entry.final,
            "outcome": entry.result,
            "reason": getattr(rec, "reason", None),
            "commands_sent": int(getattr(rec, "commands_sent", 0) or 0),
            "mp4": recording.path if recording is not None else None,
            "recording": recording.as_dict() if recording is not None else None,
            "elapsed_s": {
                "understanding": understanding_s,
                "run": run_s,
                "total": round((understanding_s or 0.0) + (run_s or 0.0), 2),
            },
            "session": self.session(),
        }

    def __call__(self, entry: LogEntry) -> None:
        if entry.path not in RUN_PATHS or entry.result.startswith(("refused", "dropped")):
            return
        row = self.row(entry)
        with self._lock:
            self.rows.append(row)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_rows(path: Path) -> list[dict[str, Any]]:
    rows = []
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise ValueError(f"{path}:{n}: not JSON: {e.msg}") from e
    return rows
