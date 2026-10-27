"""Uc acceptance set and the G2 scorer.

    uv run python -m so101_demo.evidence.uc_eval --check-manifest
    uv run python -m so101_demo.evidence.uc_eval --cut tmp/uc-movie     # rebuild the videos
    uv run python -m so101_demo.evidence.uc_eval --frames               # 1 fps frame sheets
    uv run python -m so101_demo.evidence.uc_eval --fix-config | --run | --report-only  # Uc eval

`--check-manifest` checks contracts/fixtures/uc/videos.json against the committed videos
(`check_manifest`: count, ids, hashes, breakdown, gold, revision log, and the file format
through ffprobe) and exits 0 only when nothing is wrong. `--cut` rebuilds every video from
its source entry (the source files stay outside the repository; their sha256 is checked
first). `--frames` writes one sheet per video with the ten frames at 0.5, 1.5, ... 9.5 s
to docs/evidence/uc/frames/, the evidence that each action lies inside its 10 s.

`score` turns evaluation rows (the shape of a row in docs/evidence/s1/diagnosis.json: one
per video and run) into the G2 numbers. It never drops a video: an in-scope video without
a row, with an invalid plan, a timeout or a refusal stays in the denominator as a failure
of its kind. The Cosmos fact check is the human check of each sentence against gold; it is
tied to the sentence, so a row whose sentence is not listed in the check is unchecked.
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..plan import PlanInvalid, validate_plan
from ..plan.paths import REPO_ROOT, UC_MANIFEST_PATH
from ..understanding.videos import sha256_file

FRAMES_DIR = REPO_ROOT / "docs" / "evidence" / "uc" / "frames"
TARGETS = ("red_cube", "blue_cube")
DESTINATIONS = ("box_a", "box_b")
COMBOS = tuple((t, d) for t in TARGETS for d in DESTINATIONS)
LAYOUTS = ("P1", "P2", "P3")
OUT_OF_SCOPE_KINDS = ("unregistered_object", "unregistered_action", "undecidable")
PER_COMBO = 3
FPS = 24
FRAMES = 240
IDS = tuple(f"uc-{n:02d}" for n in range(1, 16))

# G2 (fixed 2026-09-19): accuracy and time limits of the fixed acceptance set.
G2_IN_SCOPE_MIN = 10
G2_PER_COMBO_MIN = 2
G2_MEDIAN_S = 60.0
G2_MAX_S = 120.0


@dataclass(frozen=True)
class UcVideo:
    id: str
    path: Path
    sha256: str
    version: int
    source: dict[str, Any]
    conditions: dict[str, Any]
    scope: str
    out_of_scope_kind: str | None
    gold: dict[str, Any]


def load_manifest(path: Path = UC_MANIFEST_PATH) -> tuple[dict[str, Any], list[UcVideo]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    videos = [
        UcVideo(
            v["id"],
            REPO_ROOT / v["path"],
            v["sha256"],
            v.get("version", 1),
            v.get("source") or {},
            v.get("conditions") or {},
            v["scope"],
            v.get("out_of_scope_kind"),
            v["gold"],
        )
        for v in raw["videos"]
    ]
    return raw, videos


def _gold_problems(v: UcVideo) -> list[str]:
    gold = {k: v.gold.get(k) for k in ("status", "target", "destination", "steps")}
    try:
        plan = validate_plan({**gold, "note": ""})
    except PlanInvalid as e:
        return [f"{v.id}: gold is not a contract plan: {e}"]
    problems = []
    if plan.executable != (v.scope == "in_scope"):
        problems.append(f"{v.id}: gold status {plan.status!r} does not fit scope {v.scope}")
    if v.scope == "in_scope":
        want = [
            {"action": "pick", "object": plan.target},
            {"action": "place", "destination": plan.destination},
        ]
        if v.gold["steps"] != want:
            problems.append(f"{v.id}: gold steps are not pick target -> place destination")
    if not v.gold.get("facts"):
        problems.append(f"{v.id}: gold facts missing")
    return problems


def _breakdown_problems(videos: list[UcVideo]) -> list[str]:
    problems = []
    in_scope = [v for v in videos if v.scope == "in_scope"]
    out_scope = [v for v in videos if v.scope == "out_of_scope"]
    for v in videos:
        if v.scope not in ("in_scope", "out_of_scope"):
            problems.append(f"{v.id}: scope {v.scope!r}")
    if len(in_scope) != len(COMBOS) * PER_COMBO:
        problems.append(f"expected {len(COMBOS) * PER_COMBO} in_scope, got {len(in_scope)}")
    for combo in COMBOS:
        vs = [v for v in in_scope if (v.gold.get("target"), v.gold.get("destination")) == combo]
        layouts = sorted(str(v.conditions.get("layout")) for v in vs)
        if layouts != list(LAYOUTS):
            problems.append(f"{combo[0]} -> {combo[1]}: layouts {layouts}, expected P1 P2 P3")
    for v in in_scope:
        if (v.gold.get("target"), v.gold.get("destination")) not in COMBOS:
            problems.append(f"{v.id}: in-scope gold is not one of the 4 combinations")
        if v.out_of_scope_kind is not None:
            problems.append(f"{v.id}: in-scope video with out_of_scope_kind")
    kinds = sorted(str(v.out_of_scope_kind) for v in out_scope)
    if kinds != sorted(OUT_OF_SCOPE_KINDS):
        problems.append(f"out_of_scope kinds {kinds}, expected one each of {OUT_OF_SCOPE_KINDS}")
    for key in ("file", "sha256"):
        src = [v.source.get(key) for v in videos]
        if None in src or len(set(src)) != len(src):
            problems.append(f"source {key} missing or repeated (every video is a separate take)")
    return problems


def _revision_problems(raw: dict[str, Any], videos: list[UcVideo]) -> list[str]:
    problems = []
    if not raw.get("revision_rule"):
        problems.append("revision_rule missing")
    revs = raw.get("revisions")
    if not isinstance(revs, list):
        return [*problems, "revisions is not a list"]
    for r in revs:
        missing = [
            k for k in ("id", "version", "date", "reason", "old_sha256", "new_sha256") if k not in r
        ]
        if missing:
            problems.append(f"revision {r.get('id')} v{r.get('version')}: missing {missing}")
    for v in videos:
        logged = {r.get("version") for r in revs if r.get("id") == v.id}
        for version in range(2, v.version + 1):
            if version not in logged:
                problems.append(f"{v.id}: version {version} has no revisions entry")
        latest = [r for r in revs if r.get("id") == v.id and r.get("version") == v.version]
        if latest and latest[-1].get("new_sha256") != v.sha256:
            problems.append(f"{v.id}: last revision new_sha256 != current sha256")
    return problems


def probe_format(path: Path) -> dict[str, Any]:
    """ffprobe's view of a cut video: streams, frame count, duration, container tags."""
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-count_frames", "-print_format", "json",
            "-show_streams", "-show_format", str(path),
        ],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    info = json.loads(out.stdout)
    streams = info.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), {})
    num, _, den = v.get("r_frame_rate", "0/1").partition("/")
    return {
        "streams": [s.get("codec_type") for s in streams],
        "codec": v.get("codec_name"),
        "width": v.get("width"),
        "height": v.get("height"),
        "fps": int(num) / int(den) if int(den or 0) else 0.0,
        "frames": int(v.get("nb_read_frames", 0)),
        "duration_s": float(v.get("duration", 0.0)),
        "rotation": [d.get("rotation") for d in v.get("side_data_list", []) if "rotation" in d],
        "tags": {**info.get("format", {}).get("tags", {}), **v.get("tags", {})},
    }


# Tags a phone or an editor leaves behind; the cut videos must carry none of them. The
# only encoder tag allowed is the one ffmpeg itself writes ("Lavc libx264").
IDENTIFYING_TAGS = ("creation_time", "location", "com.apple", "com.android", "encoder", "title")
OWN_ENCODER = "Lavc"


def _format_problems(v: UcVideo, fmt: dict[str, Any]) -> list[str]:
    want = {"codec": "h264", "width": 1280, "height": 720, "fps": float(FPS), "frames": FRAMES}
    problems = [f"{v.id}: {k} {fmt[k]!r} != {w!r}" for k, w in want.items() if fmt[k] != w]
    if abs(fmt["duration_s"] - FRAMES / FPS) > 1e-3:
        problems.append(f"{v.id}: duration {fmt['duration_s']} s != 10.0 s")
    if fmt["streams"] != ["video"]:
        problems.append(f"{v.id}: streams {fmt['streams']} (expected one video stream)")
    if fmt["rotation"]:
        problems.append(f"{v.id}: rotation side data {fmt['rotation']}")
    bad = [
        t
        for t, value in fmt["tags"].items()
        if any(t.lower().startswith(p) for p in IDENTIFYING_TAGS)
        and not (t == "encoder" and str(value).startswith(OWN_ENCODER))
    ]
    if bad:
        problems.append(f"{v.id}: metadata tags {bad}")
    return problems


def check_manifest(path: Path = UC_MANIFEST_PATH, *, probe=probe_format) -> list[str]:
    """Every rule the manifest breaks (empty = valid); `probe=None` skips the format check."""
    raw, videos = load_manifest(path)
    problems: list[str] = []
    ids = [v.id for v in videos]
    if tuple(ids) != IDS:
        problems.append(f"ids {ids}, expected uc-01 .. uc-15 in order")
    if not raw.get("use") or "development_set" not in raw:
        problems.append("the acceptance / development split (use, development_set) is missing")
    problems += _breakdown_problems(videos)
    problems += _revision_problems(raw, videos)
    for v in videos:
        problems += _gold_problems(v)
        if not v.path.is_file():
            problems.append(f"{v.id}: missing file {v.path.relative_to(REPO_ROOT)}")
            continue
        if (actual := sha256_file(v.path)) != v.sha256:
            problems.append(f"{v.id}: sha256 {actual} != manifest {v.sha256}")
        if probe is not None:
            problems += _format_problems(v, probe(v.path))
    return problems


def cut_command(v: UcVideo, source: Path) -> list[str]:
    start = round(float(v.source["trim_start_s"]) * FPS)
    pad = int(v.source.get("pad_start_frames", 0))
    vf = (f"tpad=start={pad}:start_mode=clone," if pad else "") + (
        f"trim=start_frame={start}:end_frame={start + FRAMES},setpts=PTS-STARTPTS,"
        "scale=1280:720,format=yuv420p"
    )
    return [
        "ffmpeg", "-v", "error", "-y", "-i", str(source), "-an", "-sn", "-dn",
        "-map_metadata", "-1", "-vf", vf, "-r", str(FPS), "-c:v", "libx264",
        "-preset", "slow", "-crf", "20", "-bitexact", "-movflags", "+faststart", str(v.path),
    ]  # fmt: skip


def cut(source_dir: Path) -> None:
    _, videos = load_manifest()
    for v in videos:
        src = source_dir / v.source["file"]
        if sha256_file(src) != v.source["sha256"]:
            raise SystemExit(f"{v.id}: {src} does not match the manifest source sha256")
        v.path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(cut_command(v, src), check=True)
        print(f"{v.id} {sha256_file(v.path)} {v.path.relative_to(REPO_ROOT)}")


def frames() -> None:
    """One 5x2 sheet per video: the frames at 0.5, 1.5, ... 9.5 s, with their time."""
    _, videos = load_manifest()
    FRAMES_DIR.mkdir(parents=True, exist_ok=True)
    for v in videos:
        out = FRAMES_DIR / f"{v.id}.jpg"
        picks = "+".join(f"eq(n\\,{FPS // 2 + FPS * k})" for k in range(FRAMES // FPS))
        vf = (
            "scale=384:-1,"
            "drawtext=text='%{pts\\:hms}':x=6:y=6:fontsize=22:fontcolor=yellow:box=1:boxcolor=black,"
            f"select={picks},tile=5x2"
        )
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-i", str(v.path), "-vf", vf, "-frames:v", "1",
             "-q:v", "4", str(out)],
            check=True,
        )  # fmt: skip
        print(out.relative_to(REPO_ROOT))


# --- scoring -------------------------------------------------------------------------

IN_SCOPE_FAILURES = ("wrong_plan", "refused", "schema_invalid", "timeout", "other_error", "missing")


def exact_match(plan: dict[str, Any] | None, gold: dict[str, Any]) -> bool:
    """Target, destination and steps all equal to gold, with status ok."""
    if not plan:
        return False
    keep = ("action", "object", "destination")
    steps = [{k: s[k] for k in keep if s.get(k) is not None} for s in plan.get("steps") or []]
    return (
        plan.get("status") == "ok"
        and (plan.get("target"), plan.get("destination")) == (gold["target"], gold["destination"])
        and steps == gold["steps"]
    )


def has_failure(row: dict[str, Any]) -> bool:
    """A stage failed; the row is a failure even if its plan happens to equal gold."""
    cosmos = row.get("cosmos") or {}
    astra = row.get("astra") or {}
    return bool(row.get("failed_stage") or cosmos.get("failure") or astra.get("failure"))


def failure_kind(row: dict[str, Any]) -> str:
    """Why an in-scope row is not a success: a stage failure first, then the plan."""
    cosmos = row.get("cosmos") or {}
    astra = row.get("astra") or {}
    failures = [f for f in (cosmos.get("failure"), astra.get("failure")) if f]
    if any(f.startswith("timeout") for f in failures):
        return "timeout"
    # astra.classify_response's names for arguments that are not a contract plan. Not
    # `contract_valid`: in diagnosis.json rows it is false for every Astra failure.
    if any(f.startswith(("contract schema", "arguments are not a JSON")) for f in failures):
        return "schema_invalid"
    if failures or row.get("failed_stage"):
        return "other_error"
    status = (row.get("plan") or {}).get("status")
    if status in ("unsupported", "needs_review"):
        return "refused"
    if status == "error":
        return "other_error"
    return "wrong_plan"


def _checked(check: dict[str, Any] | None, sentence: str | None) -> bool:
    return bool(check) and sentence is not None and sentence in check.get("sentences", [])


def score(
    rows: list[dict[str, Any]],
    videos: list[UcVideo],
    human_check: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """G2 numbers for one evaluation pass: one row per manifest video.

    `human_check` maps a video id to {"sentences": [...], "facts_match": bool}, written by
    a person; a row whose sentence is not in its video's list counts as unchecked.
    """
    by_id: dict[str, dict[str, Any]] = {}
    for r in rows:
        if r["video"] in by_id:
            raise ValueError(f"two rows for {r['video']}; score one run at a time")
        by_id[r["video"]] = r
    unknown = sorted(set(by_id) - {v.id for v in videos})
    if unknown:
        raise ValueError(f"rows for videos not in the manifest: {unknown}")
    human_check = human_check or {}

    in_scope = [v for v in videos if v.scope == "in_scope"]
    out_scope = [v for v in videos if v.scope == "out_of_scope"]
    per_video: dict[str, dict[str, Any]] = {}
    failures = dict.fromkeys(IN_SCOPE_FAILURES, 0)
    for v in in_scope:
        row = by_id.get(v.id)
        if row is None:
            kind = "missing"
        elif has_failure(row) or not exact_match(row.get("plan"), v.gold):
            kind = failure_kind(row)
        else:
            kind = None
        ok = kind is None
        if kind:
            failures[kind] += 1
        per_video[v.id] = {"exact": ok, "failure": kind}
    combos = {
        f"{t}->{d}": sum(
            per_video[v.id]["exact"]
            for v in in_scope
            if (v.gold["target"], v.gold["destination"]) == (t, d)
        )
        for t, d in COMBOS
    }
    false_accepts = [v.id for v in out_scope if (by_id.get(v.id) or {}).get("executable")]
    out_missing = [v.id for v in out_scope if v.id not in by_id]

    times = [r["total_s"] for r in by_id.values() if r.get("total_s") is not None]
    facts_match = facts_mismatch = 0
    unchecked: list[str] = []
    for v in videos:
        row = by_id.get(v.id)
        sentence = ((row or {}).get("cosmos") or {}).get("sentence")
        if row is None or not sentence:
            continue
        check = human_check.get(v.id)
        if not _checked(check, sentence):
            unchecked.append(v.id)
        elif check.get("facts_match"):
            facts_match += 1
        else:
            facts_mismatch += 1

    exact = sum(p["exact"] for p in per_video.values())
    return {
        "in_scope_exact": exact,
        "in_scope_total": len(in_scope),
        "per_combo": combos,
        "per_combo_total": PER_COMBO,
        "in_scope_failures": failures,
        "out_of_scope_false_accepts": len(false_accepts),
        "out_of_scope_false_accept_ids": false_accepts,
        "out_of_scope_total": len(out_scope),
        "out_of_scope_missing": out_missing,
        "latency_s": {
            "n": len(times),
            "median": statistics.median(times) if times else None,
            "max": max(times) if times else None,
        },
        "cosmos_facts": {
            "match": facts_match,
            "mismatch": facts_mismatch,
            "unchecked": unchecked,
            "sentences": sum(1 for r in by_id.values() if (r.get("cosmos") or {}).get("sentence")),
        },
        "per_video": per_video,
        "g2_accuracy_pass": exact >= G2_IN_SCOPE_MIN
        and all(n >= G2_PER_COMBO_MIN for n in combos.values())
        and not false_accepts
        and not out_missing,
        "g2_latency_pass": bool(times)
        and len(times) == len(videos)
        and statistics.median(times) <= G2_MEDIAN_S
        and max(times) <= G2_MAX_S,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check-manifest", action="store_true")
    g.add_argument("--cut", type=Path, metavar="SOURCE_DIR")
    g.add_argument("--frames", action="store_true")
    g.add_argument("--fix-config", action="store_true", help="write config.json")
    g.add_argument("--run", action="store_true", help="evaluate the 15 videos (GPU machine)")
    g.add_argument("--report-only", action="store_true", help="rebuild eval.md from the json")
    ap.add_argument("--revision", type=int, default=1)
    args = ap.parse_args(argv)
    if args.fix_config or args.run or args.report_only:
        from . import uc_report  # imports this module; kept out of the manifest commands

        if args.fix_config:
            print(uc_report.fix_config(args.revision).relative_to(REPO_ROOT))
            return 0
        if args.run:
            uc_report.run(args.revision)
        for path in uc_report.write_reports():
            print(path.relative_to(REPO_ROOT))
        return 0
    if args.cut:
        cut(args.cut)
        return 0
    if args.frames:
        frames()
        return 0
    problems = check_manifest()
    for p in problems:
        print(p)
    if not problems:
        _, videos = load_manifest()
        print(f"manifest OK: {len(videos)} videos, hashes, breakdown and format match")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
