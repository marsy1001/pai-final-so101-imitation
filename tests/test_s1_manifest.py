"""contracts/fixtures/s1/videos.json matches the committed videos."""

import json

from so101_demo.plan.paths import S1_MANIFEST_PATH
from so101_demo.understanding.videos import check_manifest, load_manifest


def test_committed_manifest_is_valid():
    assert check_manifest() == []


def test_breakdown_two_in_scope_pairs_and_one_out_of_scope_kind():
    _, videos = load_manifest()
    in_scope = [v for v in videos if v.scope == "in_scope"]
    out_scope = [v for v in videos if v.scope == "out_of_scope"]
    assert len(in_scope) == 2 and len(out_scope) == 1
    assert len({(v.gold["target"], v.gold["destination"]) for v in in_scope}) == 2
    assert out_scope[0].out_of_scope_kind == "unregistered_action"


def _write(tmp_path, mutate):
    raw = json.loads(S1_MANIFEST_PATH.read_text(encoding="utf-8"))
    mutate(raw)
    p = tmp_path / "videos.json"
    p.write_text(json.dumps(raw), encoding="utf-8")
    return p


def test_hash_mismatch_is_reported(tmp_path):
    p = _write(tmp_path, lambda r: r["videos"][0].update(sha256="0" * 64))
    assert any("s1-01: sha256" in m for m in check_manifest(p))


def test_same_pair_twice_is_reported(tmp_path):
    def mutate(r):
        r["videos"][1]["gold"] = r["videos"][0]["gold"]

    assert any(
        "repeat a (target, destination)" in m for m in check_manifest(_write(tmp_path, mutate))
    )


def test_out_of_scope_gold_must_not_be_executable(tmp_path):
    def mutate(r):
        r["videos"][2]["gold"] = dict(r["videos"][0]["gold"])
        r["videos"][2]["gold"]["target"] = "blue_cube"
        r["videos"][2]["gold"]["steps"] = [
            {"action": "pick", "object": "blue_cube"},
            {"action": "place", "destination": "box_a"},
        ]

    assert any("s1-03: gold status 'ok'" in m for m in check_manifest(_write(tmp_path, mutate)))


def test_missing_kind_is_reported(tmp_path):
    p = _write(tmp_path, lambda r: r["videos"][2].update(out_of_scope_kind=None))
    assert any("out_of_scope_kind None" in m for m in check_manifest(p))
