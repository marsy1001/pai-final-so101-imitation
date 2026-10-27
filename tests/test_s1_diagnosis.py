"""S1 diagnosis evidence (Issue #22 GPU part).

The first tests read the committed evidence and need nothing; the `gpu` ones talk to the
running Cosmos server (serving/cosmos/serve.sh) and check the client and server agree on
the request shape (`mm_processor_kwargs`, `reasoning_content`).
"""

from __future__ import annotations

import copy
import json
import urllib.request

import pytest

from so101_demo.evidence import s1_diagnosis as D
from so101_demo.plan import validate_plan
from so101_demo.understanding import cosmos
from so101_demo.understanding import settings as S
from so101_demo.understanding.videos import load_manifest

DATA = json.loads((D.OUT_DIR / "diagnosis.json").read_text(encoding="utf-8"))
_, VIDEOS = load_manifest()


def test_diagnosis_covers_every_manifest_video_every_run():
    ids = [v.id for v in VIDEOS]
    assert [v["id"] for v in DATA["videos"]] == ids
    assert sorted((r["video"], r["run"]) for r in DATA["rows"]) == sorted(
        (i, n) for i in ids for n in range(1, DATA["runs"] + 1)
    )
    for v, m in zip(DATA["videos"], VIDEOS, strict=True):
        assert v["sha256"] == m.sha256 and "error" not in v["processor"]


def test_diagnosis_plans_are_contract_plans_and_out_of_scope_never_executable():
    scope = {v.id: v.scope for v in VIDEOS}
    for r in DATA["rows"]:
        plan = validate_plan(r["plan"])
        assert plan.executable == r["executable"]
        if scope[r["video"]] != "in_scope":
            assert not r["executable"]


def test_diagnosis_md_is_current_and_carries_the_human_check(tmp_path, monkeypatch):
    committed = (D.OUT_DIR / "diagnosis.md").read_text(encoding="utf-8")
    monkeypatch.setattr(D, "OUT_DIR", tmp_path)
    (tmp_path / "diagnosis.json").write_text(json.dumps(DATA), encoding="utf-8")
    D.write_diagnosis()
    assert (tmp_path / "diagnosis.md").read_text(encoding="utf-8") == committed
    human = json.loads(D.HUMAN_CHECK.read_text(encoding="utf-8"))
    assert set(human["videos"]) == {v.id for v in VIDEOS}
    assert committed.startswith("# ") and "\n## 結論\n" in committed
    assert "未照合" not in committed


def _render(tmp_path, monkeypatch, rows) -> str:
    """diagnosis.md for a copy of the stored data with `rows` swapped in."""
    data = copy.deepcopy(DATA)
    data["rows"] = rows
    monkeypatch.setattr(D, "OUT_DIR", tmp_path)
    (tmp_path / "diagnosis.json").write_text(json.dumps(data), encoding="utf-8")
    D.write_diagnosis()
    return (tmp_path / "diagnosis.md").read_text(encoding="utf-8")


def _conclusion(md: str) -> str:
    return md.split("\n## 結論\n", 1)[1].split("\n## ", 1)[0]


def test_report_is_written_when_every_cosmos_call_fails(tmp_path, monkeypatch):
    rows = copy.deepcopy(DATA["rows"])
    for r in rows:
        r["cosmos"].update(
            sentence=None,
            failure="timeout",
            finish_reason=None,
            reasoning=None,
            prompt_tokens=None,
            completion_tokens=None,
        )
        r.update(astra=None, failed_stage="cosmos", executable=False, matches_gold=False)
    md = _conclusion(_render(tmp_path, monkeypatch, rows))
    assert "0/9 実行で一文を返した" in md and "timeout 9" in md
    assert "Astra 呼んでいない（Cosmos が失敗）" in md
    assert "s1-01 の Cosmos 段" in md and "Astra 段" not in md
    assert "Cosmos 成立" not in md and "Astra 成立" not in md


def test_astra_failures_make_the_astra_stage_fail(tmp_path, monkeypatch):
    rows = copy.deepcopy(DATA["rows"])
    for r in rows:
        r["astra"].update(api_status=None, failure="timeout", contract_valid=False)
        r.update(failed_stage="astra", executable=False, matches_gold=False)
    md = _conclusion(_render(tmp_path, monkeypatch, rows))
    assert "Astra 未成立（契約検証 0/3、失敗: timeout 3）" in md
    assert "s1-01 の Astra 段" in md and "未成立の段: なし" not in md


def test_hand_check_does_not_carry_over_to_new_sentences(tmp_path, monkeypatch):
    rows = copy.deepcopy(DATA["rows"])
    rows[-1]["cosmos"]["sentence"] = "A hand puts both cubes into the right tray."
    assert rows[-1]["video"] == "s1-03"
    md = _render(tmp_path, monkeypatch, rows)
    assert "s1-03（out_of_scope）: Cosmos 未照合" in _conclusion(md)
    s3 = md.split("\n## s1-03", 1)[1].split("\n## ", 1)[0]
    assert "照合（gold と映像、未照合）" in s3 and "ユーザー確認済み" not in s3


def test_every_hand_check_names_the_sentences_it_was_made_on():
    human = json.loads(D.HUMAN_CHECK.read_text(encoding="utf-8"))
    for v in DATA["videos"]:
        sentences = {r["cosmos"]["sentence"] for r in DATA["rows"] if r["video"] == v["id"]}
        assert D.check_matches(human["videos"][v["id"]], sentences), v["id"]


def test_fixed_operational_values_are_the_ones_measured():
    assert DATA["settings"]["COSMOS_MAX_TOKENS"] == S.COSMOS_MAX_TOKENS
    assert DATA["settings"]["COSMOS_TIMEOUT_S"] == S.COSMOS_TIMEOUT_S
    assert DATA["settings"]["ASTRA_TIMEOUT_S"] == S.ASTRA_TIMEOUT_S
    worst = max(r["cosmos"]["completion_tokens"] for r in DATA["rows"])
    assert worst * 2 < S.COSMOS_MAX_TOKENS
    assert max(r["cosmos"]["elapsed_s"] for r in DATA["rows"]) * 2 < S.COSMOS_TIMEOUT_S
    assert max(r["astra"]["elapsed_s"] for r in DATA["rows"] if r["astra"]) * 2 < S.ASTRA_TIMEOUT_S


def _server_up() -> bool:
    try:
        with urllib.request.urlopen(S.COSMOS_BASE_URL + "/health", timeout=2):
            return True
    except OSError:
        return False


@pytest.mark.gpu
def test_cosmos_server_answers_one_sentence_for_a_manifest_video():
    if not _server_up():
        pytest.fail(f"Cosmos server not running at {S.COSMOS_BASE_URL} (serving/cosmos/serve.sh)")
    video = VIDEOS[0]
    r = cosmos.describe(video.path)
    assert r.ok, r.failure
    assert r.finish_reason == "stop" and r.sentence and "<think>" not in r.sentence
    assert r.reasoning, "reasoning_content is empty: --reasoning-parser qwen3 not applied?"
    # the server applied fps=4 and the pixel budget exactly as the processor probe says
    probe = next(v["processor"] for v in DATA["videos"] if v["id"] == video.id)
    # the S1 rows used the prompt without the scene (#25); the Uc revision fixed with the
    # current prompt ran the same 10 s format and gives the expected count
    from so101_demo.evidence import uc_report

    rev = next(
        n
        for n in reversed(uc_report.revisions())
        if json.loads(uc_report.paths(n)["config"].read_text(encoding="utf-8"))["cosmos"]["prompt"]
        == cosmos.SENTENCE_PROMPT
    )
    uc_rows = json.loads(uc_report.paths(rev)["eval"].read_text(encoding="utf-8"))["rows"]
    assert {row["cosmos"]["prompt_tokens"] for row in uc_rows} == {r.usage["prompt_tokens"]}
    assert r.usage["prompt_tokens"] > probe["video_tokens"]
