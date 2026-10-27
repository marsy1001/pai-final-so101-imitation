"""GPT-6 Astra client: one sentence in, one candidate plan out (Responses API, Azure OpenAI).

Astra sees only Cosmos' sentence and the registered ids; no video, no image (by design).
It must answer by calling the `submit_plan` function tool exactly once. The tool call is
the receipt of a *candidate* plan, never a permission to run it: the arguments are
validated here against the full contract, then approved by a person, then checked by the
safety layer.

Two schemas, on purpose:

- `submit_plan_tool()` is the strict-mode subset the API accepts: `strict: true`, every
  object has `additionalProperties: false`, every property is `required`, the optional
  values are nullable (`type: [..., "null"]` with `null` in the `enum`), no root `anyOf`,
  no `if` / `then` / `oneOf`, no `maxLength`. It guarantees the *shape* only.
- `contracts/plan.schema.json` is the full contract (Draft 2020-12, `allOf` / `if` /
  `oneOf`): `ok` names one registered pair and its steps repeat exactly that pair,
  every other status carries no steps, `note` is at most 200 characters. Strict mode
  cannot express these cross-field rules, so `classify_response` re-validates every
  argument set with `validate_plan` and turns a violation into `status: error`.

Strict mode does not make Astra recognise an unsupported task correctly (a sentence
about a green cylinder can still come back as `red_cube`); `status` and the nulls are the
way out, and the human check against gold is what measures it (the 2026-09-28 GPU runs).

Everything that is not a completed response with exactly one `submit_plan` call whose
arguments pass the contract becomes an error plan (`status: error`, not executable):
timeout, transport / API error, `status` failed / incomplete / anything but completed,
an `output` or message content of the wrong shape, refusal, no tool call, several tool
calls, arguments that are not JSON, and contract violations. A refusal is an error, not
`unsupported` (understanding-layer-notes §3).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from ..plan import Plan, PlanInvalid, load_registry, load_schema, validate_plan
from . import settings as S
from .transport import Post, TransportError, TransportTimeout, load_env, post_json

TOOL_NAME = "submit_plan"

# How the registered ids look in the S1 videos (contracts/fixtures/s1/videos.json:
# left / right as seen in the frame). Cosmos names trays by position, so the mapping is
# given to Astra in the same words.
ID_DESCRIPTIONS = {
    "red_cube": "the red cube",
    "blue_cube": "the blue cube",
    "box_a": "the container / tray / box on the LEFT side of the image",
    "box_b": "the container / tray / box on the RIGHT side of the image",
}

INSTRUCTIONS = """\
You turn ONE sentence that describes a short video into a candidate plan for a robot demo.
Answer only by calling submit_plan exactly once.

You do not see the video. The sentence is all you know. Never add an object, a place or an
action the sentence does not state, and never pick a registered id just because it is the
closest one.

Registered objects:
{objects}
Registered destinations:
{destinations}
The only supported task: a registered object is picked up and put into a registered
destination.

- status ok: the sentence says a registered object was put into a registered destination.
  Set target and destination, and steps = [pick target, place destination].
- status unsupported: the sentence describes something outside the supported task: an
  object that is not registered, or an action other than putting a registered object into a
  destination (for example lifting it and putting it back, pushing it, stacking).
  target = null, destination = null, steps = [].
- status needs_review: the task looks supported but the object or the destination is
  missing, ambiguous or uncertain in the sentence. Set target / destination only when the
  sentence states them clearly, otherwise null. steps = [].
- status error: only when the sentence is empty or unreadable. target / destination null,
  steps = [].
- note: one short Japanese sentence (at most 80 characters) for the visitor, saying what
  was understood or why it cannot be run.
"""


@dataclass(frozen=True)
class AstraResult:
    plan: Plan  # always a valid contract plan; an error plan when `failure` is set
    failure: str | None
    api_status: str | None
    raw_arguments: str | None
    elapsed_s: float
    response_id: str | None = None
    model: str | None = None  # the model behind the deployment, as the API reports it
    usage: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.failure is None


def _nullable_enum(values: list[str]) -> dict[str, Any]:
    return {"type": ["string", "null"], "enum": [*values, None]}


def submit_plan_tool() -> dict[str, Any]:
    """The strict-mode function tool. Ids come from contracts/registry.json and the status
    list from contracts/plan.schema.json, so the tool cannot drift from the contract."""
    reg = load_registry()
    objects = list(reg.objects)
    destinations = list(reg.destinations)
    statuses = load_schema()["properties"]["status"]["enum"]
    pick = {
        "type": "object",
        "additionalProperties": False,
        "required": ["action", "object"],
        "properties": {
            "action": {"type": "string", "enum": ["pick"]},
            "object": {"type": "string", "enum": objects},
        },
    }
    place = {
        "type": "object",
        "additionalProperties": False,
        "required": ["action", "destination"],
        "properties": {
            "action": {"type": "string", "enum": ["place"]},
            "destination": {"type": "string", "enum": destinations},
        },
    }
    return {
        "type": "function",
        "name": TOOL_NAME,
        "description": (
            "Submit the candidate plan read from the sentence. Receiving it does not run "
            "anything: the plan is validated and a person approves it first."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "required": ["status", "target", "destination", "steps", "note"],
            "properties": {
                "status": {"type": "string", "enum": list(statuses)},
                "target": _nullable_enum(objects),
                "destination": _nullable_enum(destinations),
                "steps": {"type": "array", "items": {"anyOf": [pick, place]}},
                "note": {"type": "string", "description": "One short Japanese sentence."},
            },
        },
    }


def instructions() -> str:
    reg = load_registry()

    def lines(ids: list[str]) -> str:
        return "\n".join(f"- {i}: {ID_DESCRIPTIONS.get(i, i)}" for i in ids)

    return INSTRUCTIONS.format(
        objects=lines(list(reg.objects)), destinations=lines(list(reg.destinations))
    )


def build_request(sentence: str, deployment: str) -> dict[str, Any]:
    return {
        "model": deployment,
        "instructions": instructions(),
        "input": f"Sentence: {sentence}",
        "tools": [submit_plan_tool()],
        "tool_choice": {"type": "function", "name": TOOL_NAME},
        "parallel_tool_calls": False,
        "store": False,
    }


def error_plan(reason: str) -> Plan:
    note = f"理解層の応答を使えませんでした（{reason}）"
    if len(note) > 200:
        note = note[:199] + "…"
    return validate_plan(
        {"status": "error", "target": None, "destination": None, "steps": [], "note": note}
    )


def classify_response(resp: dict[str, Any]) -> tuple[Plan, str | None, str | None]:
    """(plan, failure, raw_arguments) from a Responses API body; see the module docstring."""
    status = resp.get("status")
    if resp.get("error"):
        return error_plan("API エラー"), f"api error: {resp['error']}", None
    if status == "incomplete":
        details = resp.get("incomplete_details")
        reason = details.get("reason") if isinstance(details, dict) else details
        return error_plan("出力が途中で打ち切られた"), f"incomplete: {reason}", None
    if status != "completed":
        return error_plan(f"応答状態 {status}"), f"status={status!r} (not 'completed')", None
    output = resp.get("output")
    if not isinstance(output, list) or not all(isinstance(i, dict) for i in output):
        return error_plan("応答の形が不正"), "output is not a list of objects", None
    for item in output:
        if item.get("type") == "message":
            content = item.get("content") or []
            if not isinstance(content, list) or not all(isinstance(p, dict) for p in content):
                return (
                    error_plan("応答の形が不正"),
                    "message content is not a list of objects",
                    None,
                )
            for part in content:
                if part.get("type") == "refusal":
                    return error_plan("応答が拒否された"), f"refusal: {part.get('refusal')}", None
    calls = [i for i in output if i.get("type") == "function_call"]
    if not calls:
        return error_plan("tool call が無い"), "no function_call in the output", None
    if len(calls) > 1:
        return error_plan("tool call が複数"), f"{len(calls)} function_calls", None
    call = calls[0]
    arguments = call.get("arguments")
    raw = arguments if isinstance(arguments, str) or arguments is None else repr(arguments)
    if call.get("name") != TOOL_NAME:
        return error_plan("想定外の tool"), f"function_call name={call.get('name')!r}", raw
    try:
        args = json.loads(arguments) if isinstance(arguments, str) else None
    except json.JSONDecodeError:
        args = None
    if not isinstance(args, dict):
        return error_plan("引数が JSON でない"), "arguments are not a JSON object", raw
    try:
        return validate_plan(args), None, raw
    except PlanInvalid as e:
        return error_plan("契約 schema 違反"), f"contract schema: {e}", raw


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _dict_or_empty(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def structure(
    sentence: str,
    *,
    env: dict[str, str] | None = None,
    post: Post = post_json,
) -> AstraResult:
    """Send one sentence to Astra and return the validated plan (an error plan on failure).

    Reads AOAI_ENDPOINT, AOAI_API_KEY and AOAI_ASTRA_DEPLOYMENT (see .env.example).
    """
    env = load_env() if env is None else env
    missing = [
        k for k in ("AOAI_ENDPOINT", "AOAI_API_KEY", "AOAI_ASTRA_DEPLOYMENT") if not env.get(k)
    ]
    if missing:
        raise RuntimeError(f"not set in .env or the environment: {', '.join(missing)}")
    url = env["AOAI_ENDPOINT"].rstrip("/") + "/openai/v1/responses"
    body = build_request(sentence, env["AOAI_ASTRA_DEPLOYMENT"])
    t0 = time.monotonic()
    try:
        resp = post(url, {"api-key": env["AOAI_API_KEY"]}, body, S.ASTRA_TIMEOUT_S)
    except TransportTimeout as e:
        plan = error_plan("タイムアウト")
        return AstraResult(plan, f"timeout: {e}", None, None, time.monotonic() - t0)
    except TransportError as e:
        plan = error_plan("API に接続できない")
        return AstraResult(plan, f"transport: {e}", None, None, time.monotonic() - t0)
    elapsed = time.monotonic() - t0
    plan, failure, raw = classify_response(resp)
    return AstraResult(
        plan,
        failure,
        _str_or_none(resp.get("status")),
        raw,
        elapsed,
        _str_or_none(resp.get("id")),
        _str_or_none(resp.get("model")),
        _dict_or_empty(resp.get("usage")),
    )
