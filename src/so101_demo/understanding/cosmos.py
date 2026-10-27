"""Cosmos Reason 2 client: one video in, one sentence out (vLLM OpenAI-compatible server).

The server is started by serving/cosmos/serve.sh (`--reasoning-parser qwen3`,
`--allowed-local-media-path`); the video goes in as a `file://` URL, before the
text (the official example puts media first). Reasoning is on and the answer is one plain
sentence: Cosmos never writes JSON (by design). The prompt asks for what was moved, where
it went and in which order, gives an escape ("cannot tell"), asks for no times and no
coordinates, and does not mention containers. It is the S1
diagnosis prompt with the scene sentence of Uc revision 2 in front.

`describe` never raises for a model or transport failure: it returns a `CosmosResult`
whose `failure` names the check that failed. Checks: timeout, transport error, a
response of the wrong shape, `finish_reason != "stop"` (a runaway that hit `max_tokens`
ends with `length`), and an empty final answer (with reasoning on, a model can leave the
answer inside the trace).
The reasoning trace is kept for the log but never used as the answer.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import settings as S
from .transport import Post, TransportError, TransportTimeout, post_json

# The S1 prompt (no word about containers, 2026-09-28, user decision on Issue #22: the first
# S1 prompt asked whether the object "ends up inside a container", and on the lift-and-return
# video Cosmos then invented two tray placements) failed Uc 6/12: without being told the
# trays, Cosmos called them "white cube" / "white box" and put things "on top of" them.
# Uc revision 2 (2026-09-29, user decision on Issue #25) states the scene as a fact before
# the S1 prompt and asks nothing about the trays (docs/evidence/uc/eval-r2.md).
SCENE = (
    "The table shows a red cube, a blue cube, and two white trays at the far edge of the "
    "table, one on the left and one on the right of the image."
)
SENTENCE_PROMPT = (
    SCENE
    + " "
    + (
        "Describe in one sentence what the person's hand does in this video: which object it "
        "moves, where it puts that object, and in what order. Name objects by colour and shape, "
        "and name places by where they are in the image (left or right). If you cannot tell "
        "what was moved or where it went, say that you cannot tell. Do not give timestamps or "
        "coordinates."
    )
)
# Official Cosmos Reason reasoning suffix (cosmos-reason2 README, "reasoning" prompts);
# with `--reasoning-parser qwen3` the trace comes back in `reasoning_content`.
REASONING_SUFFIX = (
    "\n\nAnswer the question using the following format:\n\n<think>\nYour reasoning.\n"
    "</think>\n\nWrite your final answer immediately after the </think> tag."
)


@dataclass(frozen=True)
class CosmosResult:
    sentence: str | None
    failure: str | None  # None = usable sentence
    finish_reason: str | None
    reasoning: str | None
    elapsed_s: float
    usage: dict[str, Any] = field(default_factory=dict)
    request: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.failure is None


def build_request(video: Path) -> dict[str, Any]:
    return {
        "model": S.COSMOS_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "video_url", "video_url": {"url": video.resolve().as_uri()}},
                    {"type": "text", "text": SENTENCE_PROMPT + REASONING_SUFFIX},
                ],
            }
        ],
        "max_tokens": S.COSMOS_MAX_TOKENS,
        "temperature": S.COSMOS_TEMPERATURE,
        "top_p": S.COSMOS_TOP_P,
        "top_k": S.COSMOS_TOP_K,
        "seed": S.COSMOS_SEED,
        "mm_processor_kwargs": {"fps": S.COSMOS_SAMPLE_FPS, "do_sample_frames": True},
    }


def final_answer(content: str | None) -> str:
    """The first answer block. With reasoning on, the same answer can come back 2-4 times
    separated by `</think>` (cosmos-measurements.md §4); only the first one is used."""
    if not content:
        return ""
    text = content
    if "<think>" in text:
        if "</think>" not in text:
            return ""  # the answer never left the trace
        text = text.split("</think>", 1)[1]
    return text.split("</think>", 1)[0].strip()


def parse_response(resp: dict[str, Any]) -> tuple[str | None, str | None, str | None, str | None]:
    """(sentence, failure, finish_reason, reasoning) from a chat completion."""
    choices = resp.get("choices") or []
    if not isinstance(choices, list):
        return None, "choices is not a list", None, None
    if not choices:
        return None, "no choices in the response", None, None
    choice = choices[0]
    if not isinstance(choice, dict):
        return None, "choices[0] is not an object", None, None
    finish = choice.get("finish_reason")
    finish = finish if isinstance(finish, str) else None if finish is None else repr(finish)
    msg = choice.get("message") or {}
    if not isinstance(msg, dict):
        return None, "message is not an object", finish, None
    reasoning = msg.get("reasoning_content") or msg.get("reasoning")
    reasoning = reasoning if isinstance(reasoning, str) else None
    content = msg.get("content")
    if content is not None and not isinstance(content, str):
        return None, "message content is not a string", finish, reasoning
    sentence = final_answer(content)
    if finish != "stop":
        return sentence or None, f"finish_reason={finish!r} (not 'stop')", finish, reasoning
    if not sentence:
        return None, "empty final answer", finish, reasoning
    return sentence, None, finish, reasoning


def describe(
    video: Path, *, base_url: str = S.COSMOS_BASE_URL, post: Post = post_json
) -> CosmosResult:
    body = build_request(video)
    url = base_url.rstrip("/") + "/v1/chat/completions"
    t0 = time.monotonic()
    try:
        resp = post(url, {}, body, S.COSMOS_TIMEOUT_S)
    except TransportTimeout as e:
        return CosmosResult(None, f"timeout: {e}", None, None, time.monotonic() - t0, {}, body)
    except TransportError as e:
        return CosmosResult(None, f"transport: {e}", None, None, time.monotonic() - t0, {}, body)
    elapsed = time.monotonic() - t0
    sentence, failure, finish, reasoning = parse_response(resp)
    usage = resp.get("usage")
    return CosmosResult(
        sentence,
        failure,
        finish,
        reasoning,
        elapsed,
        usage if isinstance(usage, dict) else {},
        body,
    )
