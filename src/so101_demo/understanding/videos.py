"""The S1 video manifest (contracts/fixtures/s1/videos.json).

`check_manifest` returns every rule the manifest breaks (empty = valid): three videos
with unique ids; each file exists and its sha256 matches; the breakdown is two in-scope
videos with different (target, destination) pairs and one out-of-scope video with a
recorded kind; each gold plan passes the contract, is executable exactly when the video
is in scope, and in scope names the same pair as the steps.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..plan import PlanInvalid, validate_plan
from ..plan.paths import REPO_ROOT, S1_MANIFEST_PATH

OUT_OF_SCOPE_KINDS = ("unregistered_object", "unregistered_action", "undecidable")
IN_SCOPE_COUNT = 2
OUT_OF_SCOPE_COUNT = 1


@dataclass(frozen=True)
class S1Video:
    id: str
    path: Path
    sha256: str
    scope: str
    out_of_scope_kind: str | None
    gold: dict[str, Any]


def load_manifest(path: Path = S1_MANIFEST_PATH) -> tuple[dict[str, Any], list[S1Video]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    videos = [
        S1Video(
            v["id"],
            REPO_ROOT / v["path"],
            v["sha256"],
            v["scope"],
            v.get("out_of_scope_kind"),
            v["gold"],
        )
        for v in raw["videos"]
    ]
    return raw, videos


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_manifest(path: Path = S1_MANIFEST_PATH) -> list[str]:
    _, videos = load_manifest(path)
    problems: list[str] = []
    ids = [v.id for v in videos]
    if len(set(ids)) != len(ids):
        problems.append(f"duplicate ids: {ids}")
    in_scope = [v for v in videos if v.scope == "in_scope"]
    out_scope = [v for v in videos if v.scope == "out_of_scope"]
    if len(in_scope) != IN_SCOPE_COUNT or len(out_scope) != OUT_OF_SCOPE_COUNT:
        problems.append(
            f"expected {IN_SCOPE_COUNT} in_scope + {OUT_OF_SCOPE_COUNT} out_of_scope, "
            f"got {len(in_scope)} + {len(out_scope)} (of {len(videos)})"
        )
    pairs = [(v.gold.get("target"), v.gold.get("destination")) for v in in_scope]
    if len(set(pairs)) != len(pairs):
        problems.append(f"in-scope videos repeat a (target, destination) pair: {pairs}")
    for v in videos:
        if v.scope not in ("in_scope", "out_of_scope"):
            problems.append(f"{v.id}: scope {v.scope!r}")
        if v.scope == "out_of_scope" and v.out_of_scope_kind not in OUT_OF_SCOPE_KINDS:
            problems.append(f"{v.id}: out_of_scope_kind {v.out_of_scope_kind!r}")
        if v.scope == "in_scope" and v.out_of_scope_kind is not None:
            problems.append(f"{v.id}: in-scope video with out_of_scope_kind")
        if not v.path.is_file():
            problems.append(f"{v.id}: missing file {v.path.relative_to(REPO_ROOT)}")
        elif (actual := sha256_file(v.path)) != v.sha256:
            problems.append(f"{v.id}: sha256 {actual} != manifest {v.sha256}")
        gold = {k: v.gold.get(k) for k in ("status", "target", "destination", "steps")}
        try:
            plan = validate_plan({**gold, "note": ""})
        except PlanInvalid as e:
            problems.append(f"{v.id}: gold is not a contract plan: {e}")
            continue
        if plan.executable != (v.scope == "in_scope"):
            problems.append(f"{v.id}: gold status {plan.status!r} does not fit scope {v.scope}")
        if not v.gold.get("facts"):
            problems.append(f"{v.id}: gold facts missing")
    return problems
