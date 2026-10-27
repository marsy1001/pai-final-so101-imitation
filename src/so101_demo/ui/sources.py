"""Where the screen's understanding results come from.

`FixedSource` replays the development fixed JSONs of 4a (`contracts/fixtures/4a/`) as if
they were understanding results, after a short delay, so the screen can be driven without
a camera, Cosmos or Astra. It is the default of `python -m so101_demo.ui`.

`VideoSource` is the real chain (Issue #3): every 読み取り開始 sends the next video of a
manifest or a directory through `understanding.pipeline.understand` (Cosmos sentence,
Astra plan) on a worker thread and hands the result to the screen with the run ID the
screen gave it. The screen settles it on its `RunGate`, so a result for a run that was
cancelled (手動, 対応外, 待機へ戻る), replaced or expired meanwhile is dropped there. Both
sources share `start(run_id, deliver) -> label`.
"""

from __future__ import annotations

import datetime as dt
import json
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from ..plan import Plan, validate_plan
from ..plan.paths import REPO_ROOT
from ..understanding import pipeline
from ..understanding.astra import error_plan

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

    def start(self, run_id: str, deliver: Deliver) -> str:
        fixture_id, plan = self.plans[self._next % len(self.plans)]
        self._next += 1
        sentence = f"（開発用の固定 JSON {fixture_id.split('-')[0]}）"

        def later() -> None:
            deliver(run_id, plan, sentence)

        t = threading.Timer(self.delay_s, later)
        t.daemon = True
        t.start()
        return fixture_id


# --- the real chain --------------------------------------------------------------------


@dataclass(frozen=True)
class Video:
    id: str
    path: Path


def load_videos(spec: Path, ids: Sequence[str] | None = None) -> list[Video]:
    """Videos from a manifest (`{"videos": [{"id", "path"}, ...]}`, paths relative to the
    repository root, as `contracts/fixtures/uc/videos.json`) or from a directory of mp4
    files (id = file name without the suffix). `ids` keeps those videos, in that order."""
    spec = Path(spec)
    if spec.is_dir():
        videos = [Video(p.stem, p.resolve()) for p in sorted(spec.glob("*.mp4"))]
    else:
        raw = json.loads(spec.read_text(encoding="utf-8"))
        videos = [Video(v["id"], (REPO_ROOT / v["path"]).resolve()) for v in raw["videos"]]
    if ids:
        by_id = {v.id: v for v in videos}
        missing = [i for i in ids if i not in by_id]
        if missing:
            raise ValueError(f"not in {spec}: {missing}")
        videos = [by_id[i] for i in ids]
    if not videos:
        raise ValueError(f"no videos in {spec}")
    for v in videos:
        if not v.path.is_file():
            raise FileNotFoundError(f"{v.id}: {v.path}")
    return videos


@dataclass
class VideoRun:
    """One reading: which video went in and what came back (for the run log)."""

    video: Video
    run_id: str
    started_at: str
    understanding: pipeline.Understanding | None = None
    error: str | None = None  # `understand` raised (no .env, ...): delivered as an error plan


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="milliseconds")


class VideoSource:
    """Delivers the videos in turn (cycling), each through `understand` on its own thread.

    `understand` is `pipeline.understand` unless a test injects another; `kw` goes to it
    (the clients' injectable arguments: `post`, `base_url`, `env`). The result is settled by
    the screen (`deliver`), not here: `understand` is called without a gate so one result
    is settled once. `runs` keeps every reading by run ID for the run log.
    """

    def __init__(
        self,
        videos: Sequence[Video],
        *,
        understand: Callable[..., pipeline.Understanding] = pipeline.understand,
        **kw,
    ) -> None:
        if not videos:
            raise ValueError("no videos")
        self.videos = list(videos)
        self.understand = understand
        self.kw = kw
        self.runs: dict[str, VideoRun] = {}
        self._next = 0
        self._lock = threading.Lock()

    def start(self, run_id: str, deliver: Deliver) -> str:
        with self._lock:
            video = self.videos[self._next % len(self.videos)]
            self._next += 1
            run = VideoRun(video, run_id, _now())
            self.runs[run_id] = run

        def work() -> None:
            try:
                u = self.understand(video.path, run_id=run_id, **self.kw)
            except Exception as e:  # noqa: BLE001 - the screen must get an answer
                run.error = f"{type(e).__name__}: {e}"
                deliver(run_id, error_plan("理解層を呼び出せなかった"), None)
                return
            run.understanding = u
            deliver(run_id, u.plan, u.cosmos.sentence)

        threading.Thread(target=work, name=f"understand-{video.id}", daemon=True).start()
        return video.id

    def run_of(self, run_id: str | None) -> VideoRun | None:
        with self._lock:
            return self.runs.get(run_id) if run_id else None
