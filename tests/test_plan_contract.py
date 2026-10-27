"""contracts/plan.schema.json, its samples and the registry stay consistent."""

import json
from pathlib import Path

import pytest

from so101_demo.plan import PlanInvalid, load_registry, load_schema, validate_plan
from so101_demo.plan.paths import PLAN_SCHEMA_PATH, REPO_ROOT, SAMPLES_DIR

SAMPLES = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in SAMPLES_DIR.glob("*.json")}


def test_four_samples_exist():
    assert set(SAMPLES) == {"ok", "unsupported", "needs_review", "error"}


def test_ok_sample_is_executable():
    plan = validate_plan(SAMPLES["ok"])
    assert plan.executable and plan.target == "red_cube" and plan.destination == "box_a"
    assert [s.action for s in plan.steps] == ["pick", "place"]


@pytest.mark.parametrize("name", ["unsupported", "needs_review", "error"])
def test_non_ok_samples_carry_their_status_and_no_steps(name):
    plan = validate_plan(SAMPLES[name])
    assert plan.status == name and not plan.executable and plan.steps == ()


@pytest.mark.parametrize(
    "mutation",
    [
        {"status": "ok", "steps": []},
        {"status": "ok", "target": None},
        {"status": "unsupported", "steps": [{"action": "pick", "object": "red_cube"}]},
        {"target": "green_ball"},
        {
            "steps": [
                {"action": "pick", "object": "blue_cube"},
                {"action": "place", "destination": "box_a"},
            ]
        },
        {"extra": 1},
        {
            "status": "ok",
            "steps": [
                {"action": "place", "destination": "box_a"},
                {"action": "pick", "object": "red_cube"},
            ],
        },
    ],
    ids=[
        "ok_without_steps",
        "ok_without_target",
        "unsupported_with_steps",
        "unregistered_id",
        "steps_disagree_with_target",
        "extra_field",
        "wrong_order",
    ],
)
def test_invalid_plans_are_rejected(mutation):
    data = {**SAMPLES["ok"], **mutation}
    with pytest.raises(PlanInvalid):
        validate_plan(data)


def test_raw_schema_rejects_steps_that_disagree_with_target():
    # The cross-field rule must hold for any Draft 2020-12 validator, without the
    # Python loader (the understanding layer validates with the schema directly).
    from jsonschema import Draft202012Validator

    v = Draft202012Validator(load_schema())
    ok = SAMPLES["ok"]
    assert list(v.iter_errors(ok)) == []
    bad = {
        **ok,
        "steps": [
            {"action": "pick", "object": "blue_cube"},
            {"action": "place", "destination": "box_a"},
        ],
    }
    assert list(v.iter_errors(bad)) != []
    bad = {**ok, "destination": "box_b"}
    assert list(v.iter_errors(bad)) != []
    for o in ("red_cube", "blue_cube"):
        for d in ("box_a", "box_b"):
            plan = {
                **ok,
                "target": o,
                "destination": d,
                "steps": [{"action": "pick", "object": o}, {"action": "place", "destination": d}],
            }
            assert list(v.iter_errors(plan)) == []


def test_schema_enums_match_registry_ids():
    schema, reg = load_schema(), load_registry()
    props = schema["properties"]
    assert set(props["target"]["enum"]) - {None} == set(reg.objects)
    assert set(props["destination"]["enum"]) - {None} == set(reg.destinations)
    pick, place = props["steps"]["items"]["oneOf"]
    assert set(pick["properties"]["object"]["enum"]) == set(reg.objects)
    assert set(place["properties"]["destination"]["enum"]) == set(reg.destinations)
    combos = schema["allOf"][0]["then"]["oneOf"]
    pairs = {
        (c["properties"]["target"]["const"], c["properties"]["destination"]["const"])
        for c in combos
    }
    assert pairs == {(o, d) for o in reg.objects for d in reg.destinations}


def test_schema_is_defined_exactly_once():
    # One canonical file at the repository root; the package only loads it.
    copies = [p for p in REPO_ROOT.rglob("plan.schema.json") if ".venv" not in p.parts]
    assert copies == [PLAN_SCHEMA_PATH]
    src = Path(REPO_ROOT / "src").rglob("*.py")
    assert not [
        p
        for p in src
        if '"needs_review"' in p.read_text(encoding="utf-8")
        and "enum" in p.read_text(encoding="utf-8")
    ]


def test_registry_fixture_matches_issue_4_and_adr_0008_decisions():
    reg = load_registry()
    assert reg.objects["red_cube"].size_m == 0.04 and reg.objects["red_cube"].mass_kg == 0.02
    assert reg.destinations["box_a"].inner_m == 0.06
    # 2026-09-20: trays at the far corners of the work area, mirrored in y
    assert reg.destinations["box_a"].center_xy_m == (0.22, 0.09)
    assert reg.destinations["box_b"].center_xy_m == (0.22, -0.09)
    assert [t.trial for t in reg.trials_0b] == ["T1", "T2", "T3", "T4", "T5"]
    assert {t.object for t in reg.trials_0b} == set(reg.objects)
    assert {t.destination for t in reg.trials_0b} == set(reg.destinations)
