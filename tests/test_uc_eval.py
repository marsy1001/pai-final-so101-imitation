"""Uc acceptance manifest and the G2 scorer (Issue #24, demo-plan Ub)."""

import json

import pytest

from so101_demo.evidence import uc_eval
from so101_demo.evidence.uc_eval import check_manifest, load_manifest, score
from so101_demo.plan.paths import UC_MANIFEST_PATH

# --- manifest ------------------------------------------------------------------------


def test_committed_manifest_is_valid_without_ffprobe():
    assert check_manifest(probe=None) == []


def _write(tmp_path, mutate):
    raw = json.loads(UC_MANIFEST_PATH.read_text(encoding="utf-8"))
    mutate(raw)
    p = tmp_path / "videos.json"
    p.write_text(json.dumps(raw), encoding="utf-8")
    return p


def _problems(tmp_path, mutate):
    return check_manifest(_write(tmp_path, mutate), probe=None)


def test_breakdown_is_4_combos_by_3_layouts_and_3_kinds():
    _, videos = load_manifest()
    in_scope = [v for v in videos if v.scope == "in_scope"]
    assert len(videos) == 15 and len(in_scope) == 12
    for combo in uc_eval.COMBOS:
        layouts = {
            v.conditions["layout"]
            for v in in_scope
            if (v.gold["target"], v.gold["destination"]) == combo
        }
        assert layouts == {"P1", "P2", "P3"}
    kinds = {v.out_of_scope_kind for v in videos if v.scope == "out_of_scope"}
    assert kinds == set(uc_eval.OUT_OF_SCOPE_KINDS)


def test_hash_mismatch_is_reported(tmp_path):
    ps = _problems(tmp_path, lambda r: r["videos"][0].update(sha256="0" * 64))
    assert any("uc-01: sha256" in m for m in ps)


def test_layout_repeated_in_a_combo_is_reported(tmp_path):
    ps = _problems(tmp_path, lambda r: r["videos"][1]["conditions"].update(layout="P1"))
    assert any("red_cube -> box_a: layouts" in m for m in ps)


def test_two_out_of_scope_videos_of_one_kind_are_reported(tmp_path):
    ps = _problems(
        tmp_path, lambda r: r["videos"][13].update(out_of_scope_kind="unregistered_object")
    )
    assert any("out_of_scope kinds" in m for m in ps)


def test_same_source_take_twice_is_reported(tmp_path):
    def mutate(r):
        r["videos"][1]["source"] = dict(r["videos"][0]["source"])

    assert any("every video is a separate take" in m for m in _problems(tmp_path, mutate))


def test_out_of_scope_gold_must_not_be_executable(tmp_path):
    def mutate(r):
        r["videos"][12]["gold"] = dict(r["videos"][0]["gold"])

    assert any("uc-13: gold status 'ok'" in m for m in _problems(tmp_path, mutate))


def test_new_version_without_revision_entry_is_reported(tmp_path):
    ps = _problems(tmp_path, lambda r: r["videos"][4].update(version=2))
    assert any("uc-05: version 2 has no revisions entry" in m for m in ps)


def test_logged_revision_is_accepted(tmp_path):
    def mutate(r):
        v = r["videos"][4]
        r["revisions"].append(
            {
                "id": v["id"],
                "version": 2,
                "date": "2026-10-01",
                "reason": "re-take",
                "old_sha256": "1" * 64,
                "new_sha256": v["sha256"],
            }
        )
        v["version"] = 2

    assert _problems(tmp_path, mutate) == []


def test_acceptance_development_split_is_written():
    raw, _ = load_manifest()
    assert raw["set"] == "acceptance"
    assert "never used to tune" in raw["use"]
    assert raw["development_set"]["ids"] == ["s1-01", "s1-02", "s1-03"]


def _fmt(**over):
    fmt = {
        "streams": ["video"],
        "codec": "h264",
        "width": 1280,
        "height": 720,
        "fps": 24.0,
        "frames": 240,
        "duration_s": 10.0,
        "rotation": [],
        "tags": {"major_brand": "isom", "encoder": "Lavc libx264"},
    }
    return {**fmt, **over}


def test_format_rules():
    _, videos = load_manifest()
    v = videos[0]
    assert uc_eval._format_problems(v, _fmt()) == []
    assert uc_eval._format_problems(v, _fmt(streams=["video", "audio"]))
    assert uc_eval._format_problems(v, _fmt(frames=229, duration_s=9.54))
    assert uc_eval._format_problems(v, _fmt(width=720, height=1280, rotation=[90]))
    assert uc_eval._format_problems(v, _fmt(tags={"creation_time": "2026-09-28"}))
    assert uc_eval._format_problems(v, _fmt(tags={"encoder": "Apple"}))


def test_cut_command_pads_only_when_asked():
    _, videos = load_manifest()
    by_id = {v.id: v for v in videos}
    cmd = " ".join(uc_eval.cut_command(by_id["uc-01"], by_id["uc-01"].path))
    assert "trim=start_frame=60:end_frame=300" in cmd and "tpad" not in cmd
    cmd = " ".join(uc_eval.cut_command(by_id["uc-05"], by_id["uc-05"].path))
    assert "tpad=start=11:start_mode=clone,trim=start_frame=0:end_frame=240" in cmd


# --- scoring: fixed outputs with known answers ---------------------------------------


def _plan(status, target=None, destination=None):
    steps = []
    if status == "ok":
        steps = [
            {"action": "pick", "object": target},
            {"action": "place", "destination": destination},
        ]
    return {"status": status, "target": target, "destination": destination, "steps": steps}


def _row(vid, plan, *, total_s=20.0, cosmos_failure=None, astra_failure=None, sentence="s"):
    failed = "cosmos" if cosmos_failure else "astra" if astra_failure else None
    return {
        "video": vid,
        "run": 1,
        "cosmos": {
            "sentence": None if cosmos_failure else f"{vid} {sentence}",
            "failure": cosmos_failure,
        },
        "astra": None
        if cosmos_failure
        else {"failure": astra_failure, "contract_valid": astra_failure is None},
        "failed_stage": failed,
        "plan": plan,
        "executable": plan["status"] == "ok",
        "total_s": total_s,
    }


def _gold_row(v, **kw):
    return _row(v.id, _plan("ok", v.gold["target"], v.gold["destination"]), **kw)


@pytest.fixture
def videos():
    return load_manifest()[1]


def test_all_correct(videos):
    rows = [
        _gold_row(v) if v.scope == "in_scope" else _row(v.id, _plan(v.gold["status"]))
        for v in videos
    ]
    s = score(rows, videos)
    assert (s["in_scope_exact"], s["in_scope_total"]) == (12, 12)
    assert set(s["per_combo"].values()) == {3}
    assert s["out_of_scope_false_accepts"] == 0
    assert sum(s["in_scope_failures"].values()) == 0
    assert s["g2_accuracy_pass"] and s["g2_latency_pass"]


def test_mixed_outputs_keep_every_failure_in_the_denominator(videos):
    """uc-01..03 red->box_a: wrong destination, invalid JSON, timeout (0/3).
    uc-04 red->box_b: refused (needs_review). uc-07 blue->box_a: Cosmos timeout.
    uc-10: other Astra error (API). uc-13 pen: executable plan = false accept.
    uc-12: no row at all. The rest correct."""
    by = {v.id: v for v in videos}
    ok_red_b = _plan("ok", "red_cube", "box_b")
    err = _plan("error")
    rows = [
        _row("uc-01", ok_red_b, total_s=30.0),
        _row("uc-02", err, astra_failure="arguments are not a JSON object", total_s=31.0),
        _row("uc-03", err, astra_failure="timeout: no complete response within 30 s", total_s=90.0),
        _row("uc-04", _plan("needs_review", "red_cube"), total_s=25.0),
        _gold_row(by["uc-05"], total_s=40.0),
        _gold_row(by["uc-06"], total_s=41.0),
        _row(
            "uc-07", err, cosmos_failure="timeout: no complete response within 60 s", total_s=130.0
        ),
        _gold_row(by["uc-08"], total_s=42.0),
        _gold_row(by["uc-09"], total_s=43.0),
        _row("uc-10", err, astra_failure="api error: {'code': 'server_error'}", total_s=20.0),
        _gold_row(by["uc-11"], total_s=44.0),
        _row("uc-13", _plan("ok", "red_cube", "box_b"), total_s=45.0),
        _row("uc-14", _plan("unsupported"), total_s=46.0),
        _row("uc-15", _plan("needs_review", "blue_cube"), total_s=47.0),
    ]
    s = score(rows, videos)
    assert (s["in_scope_exact"], s["in_scope_total"]) == (5, 12)
    assert s["per_combo"] == {
        "red_cube->box_a": 0,
        "red_cube->box_b": 2,
        "blue_cube->box_a": 2,
        "blue_cube->box_b": 1,
    }
    assert s["in_scope_failures"] == {
        "wrong_plan": 1,
        "refused": 1,
        "schema_invalid": 1,
        "timeout": 2,
        "other_error": 1,
        "missing": 1,
    }
    assert s["per_video"]["uc-12"] == {"exact": False, "failure": "missing"}
    assert s["out_of_scope_false_accepts"] == 1
    assert s["out_of_scope_false_accept_ids"] == ["uc-13"]
    assert s["latency_s"] == {"n": 14, "median": 42.5, "max": 130.0}
    assert not s["g2_accuracy_pass"] and not s["g2_latency_pass"]
    assert s["cosmos_facts"]["sentences"] == 13  # the Cosmos timeout has no sentence


def test_contract_violation_counts_as_schema_invalid(videos):
    rows = [_row("uc-01", _plan("error"), astra_failure="contract schema: 'target' bad")]
    s = score(rows, videos)
    assert s["in_scope_failures"]["schema_invalid"] == 1
    assert s["in_scope_failures"]["missing"] == 11


def test_failure_wins_over_a_gold_shaped_plan(videos):
    """A row that reports a stage failure is a failure even if its plan equals gold."""
    rows = [_gold_row(v) for v in videos if v.scope == "in_scope"]
    rows[0]["failed_stage"] = "astra"
    rows[0]["astra"] = {"failure": "api error: server_error", "contract_valid": False}
    rows[1]["cosmos"]["failure"] = "timeout: no complete response within 60 s"
    rows[2]["astra"] = {"failure": "contract schema: bad", "contract_valid": False}
    s = score(rows, videos)
    assert s["in_scope_exact"] == 9
    assert s["per_video"]["uc-01"] == {"exact": False, "failure": "other_error"}
    assert s["per_video"]["uc-02"] == {"exact": False, "failure": "timeout"}
    assert s["per_video"]["uc-03"] == {"exact": False, "failure": "schema_invalid"}
    assert s["per_combo"]["red_cube->box_a"] == 0


def test_exact_match_needs_the_steps(videos):
    v = videos[0]
    plan = _plan("ok", v.gold["target"], v.gold["destination"])
    plan["steps"] = plan["steps"][:1]
    s = score([_row(v.id, plan)], videos)
    assert s["per_video"]["uc-01"] == {"exact": False, "failure": "wrong_plan"}


def test_g2_pass_at_the_limits(videos):
    """10/12 with every combo 2/3, no false accept, median 60 s, max 120 s: passes."""
    wrong = {"uc-01", "uc-04"}
    rows = []
    for v in videos:
        t = 120.0 if v.id == "uc-15" else 60.0
        if v.scope == "out_of_scope":
            rows.append(_row(v.id, _plan(v.gold["status"]), total_s=t))
        elif v.id in wrong:
            rows.append(_row(v.id, _plan("unsupported"), total_s=t))
        else:
            rows.append(_gold_row(v, total_s=t))
    s = score(rows, videos)
    assert s["in_scope_exact"] == 10 and s["g2_accuracy_pass"] and s["g2_latency_pass"]
    rows[1] = _row("uc-02", _plan("unsupported"), total_s=60.0)  # red->box_a now 1/3
    assert not score(rows, videos)["g2_accuracy_pass"]


def test_human_fact_check_is_tied_to_the_sentence(videos):
    rows = [_gold_row(v) for v in videos if v.scope == "in_scope"][:3]
    check = {
        "uc-01": {"sentences": ["uc-01 s"], "facts_match": True},
        "uc-02": {"sentences": ["uc-02 s"], "facts_match": False},
        "uc-03": {"sentences": ["an older sentence"], "facts_match": True},
    }
    f = score(rows, videos, check)["cosmos_facts"]
    assert (f["match"], f["mismatch"], f["unchecked"]) == (1, 1, ["uc-03"])


def test_duplicate_or_unknown_rows_are_rejected(videos):
    r = _gold_row(videos[0])
    with pytest.raises(ValueError, match="two rows"):
        score([r, r], videos)
    with pytest.raises(ValueError, match="not in the manifest"):
        score([{**r, "video": "s1-01"}], videos)


# --- Uc evaluation report (Issue #25) ------------------------------------------------


def test_committed_reports_are_rendered_from_the_committed_json():
    from so101_demo.evidence import uc_report

    for rev in uc_report.revisions():
        p = uc_report.paths(rev)
        if p["eval"].is_file():
            assert uc_report.render(rev) == p["report"].read_text(encoding="utf-8")


def test_unconfirmed_human_check_counts_nothing():
    from so101_demo.evidence import uc_report

    if not uc_report.paths(1)["eval"].is_file():
        pytest.skip("no evaluation yet")
    human = json.loads(uc_report.paths(1)["human"].read_text(encoding="utf-8"))
    text = uc_report.render(1)
    if not human.get("user_confirmed"):
        assert "ユーザー確認待ち" in text and "事実一致 **" not in text


def test_config_pins_the_astra_deployment_without_its_name():
    from so101_demo.evidence import uc_report

    def probe(_):
        return {"sampled_frames": 40, "frame_hw": [576, 1056], "pixel_budget": {"min": 1}}

    env = {"AOAI_ENDPOINT": "https://example.invalid", "AOAI_ASTRA_DEPLOYMENT": "deploy-a"}
    a = uc_report.build_config(2, probe=probe, env=env)
    b = uc_report.build_config(2, probe=probe, env={**env, "AOAI_ASTRA_DEPLOYMENT": "deploy-b"})
    assert a["astra"]["deployment_sha256"] != b["astra"]["deployment_sha256"]
    assert "deploy-a" not in json.dumps(a)
    assert not any(".env" in r for r in uc_report.unfixable(a))
    for missing in ("AOAI_ENDPOINT", "AOAI_ASTRA_DEPLOYMENT"):
        env_missing = {k: v for k, v in env.items() if k != missing}
        cfg = uc_report.build_config(2, probe=probe, env=env_missing)
        assert f"no {missing} in .env" in uc_report.unfixable(cfg)
