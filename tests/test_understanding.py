"""Understanding-layer clients without the network (Issue #22 XPS part).

The HTTP layer is replaced by a fake `post`; nothing here calls Cosmos or Astra.
"""

import http.client
import http.server
import json
import threading
import time
import urllib.error
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from so101_demo.plan import load_registry, load_schema
from so101_demo.plan.paths import REPO_ROOT
from so101_demo.understanding import astra, cosmos, pipeline, transport
from so101_demo.understanding import settings as S
from so101_demo.understanding.transport import TransportError, TransportTimeout

ENV = {
    "AOAI_ENDPOINT": "https://example.openai.azure.com/",
    "AOAI_API_KEY": "test-key",
    "AOAI_ASTRA_DEPLOYMENT": "astra-test",
}
VIDEO = REPO_ROOT / "docs/evidence/s1/videos/s1-01-red-to-box_a.mp4"
OK_ARGS = {
    "status": "ok",
    "target": "red_cube",
    "destination": "box_a",
    "steps": [
        {"action": "pick", "object": "red_cube"},
        {"action": "place", "destination": "box_a"},
    ],
    "note": "赤いキューブを A の箱へ",
}


class FakePost:
    """Records every call and returns (or raises) the scripted responses in order."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, headers, body, timeout):
        self.calls.append({"url": url, "headers": headers, "body": body, "timeout": timeout})
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def responses_body(*output, status="completed", **extra):
    return {
        "id": "resp_x",
        "model": "gpt-6-astra",
        "status": status,
        "output": list(output),
        **extra,
    }


def call(args, name="submit_plan"):
    raw = args if isinstance(args, str) else json.dumps(args)
    return {"type": "function_call", "name": name, "arguments": raw, "call_id": "c1"}


def chat_body(content, finish="stop", reasoning="thinking"):
    return {
        "choices": [
            {
                "finish_reason": finish,
                "message": {"content": content, "reasoning_content": reasoning},
            }
        ],
        "usage": {"completion_tokens": 42},
    }


# --- submit_plan: strict-mode subset --------------------------------------------------


def walk(node, path="$"):
    if isinstance(node, dict):
        yield path, node
        for k, v in node.items():
            yield from walk(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk(v, f"{path}[{i}]")


def test_tool_is_strict_function_tool():
    tool = astra.submit_plan_tool()
    assert tool["type"] == "function" and tool["name"] == "submit_plan"
    assert tool["strict"] is True
    params = tool["parameters"]
    assert params["type"] == "object"
    assert "anyOf" not in params  # strict mode rejects a root anyOf


def test_every_object_closes_and_requires_all_properties():
    objects = [
        (p, n) for p, n in walk(astra.submit_plan_tool()["parameters"]) if n.get("type") == "object"
    ]
    assert len(objects) == 3  # root, pick step, place step
    for path, node in objects:
        assert node["additionalProperties"] is False, path
        assert sorted(node["required"]) == sorted(node["properties"]), path


def test_optional_values_are_nullable_not_omitted():
    props = astra.submit_plan_tool()["parameters"]["properties"]
    for key in ("target", "destination"):
        assert props[key]["type"] == ["string", "null"]
        assert None in props[key]["enum"]
    assert props["status"]["type"] == "string"


def test_tool_uses_no_keyword_outside_the_strict_subset():
    banned = {"if", "then", "else", "oneOf", "allOf", "not", "maxLength", "prefixItems"}
    for path, node in walk(astra.submit_plan_tool()["parameters"]):
        assert not banned & set(node), path


def test_request_names_the_tool_and_disables_parallel_calls():
    body = astra.build_request("a sentence", "astra-test")
    assert body["tool_choice"] == {"type": "function", "name": "submit_plan"}
    assert body["parallel_tool_calls"] is False
    assert body["tools"] == [astra.submit_plan_tool()]
    assert body["model"] == "astra-test"
    # Astra gets the sentence only: no video, no image
    assert body["input"] == "Sentence: a sentence"


def test_tool_ids_come_from_the_registry_and_the_contract():
    reg = load_registry()
    contract = load_schema()["properties"]
    props = astra.submit_plan_tool()["parameters"]["properties"]
    assert props["status"]["enum"] == contract["status"]["enum"]
    assert props["target"]["enum"] == [*reg.objects, None] == contract["target"]["enum"]
    assert props["destination"]["enum"] == [*reg.destinations, None]
    assert props["destination"]["enum"] == contract["destination"]["enum"]


# --- the tool schema is not the contract; the app re-validates ------------------------


def test_contract_uses_cross_field_rules_the_tool_cannot():
    contract_keys = {k for _, n in walk(load_schema()) for k in n}
    assert {"if", "then", "else", "oneOf", "allOf"} <= contract_keys


@pytest.mark.parametrize(
    "args",
    [
        {**OK_ARGS, "destination": None},  # ok without a destination
        {**OK_ARGS, "destination": "box_b"},  # steps say box_a, destination says box_b
        {**OK_ARGS, "status": "unsupported"},  # not ok but carries steps
        {**OK_ARGS, "steps": OK_ARGS["steps"][::-1]},  # place before pick
        {**OK_ARGS, "note": "あ" * 201},  # note over 200 characters
    ],
)
def test_tool_shaped_but_contract_invalid_arguments_become_error(args):
    # passes the strict tool schema ...
    Draft202012Validator(astra.submit_plan_tool()["parameters"]).validate(args)
    # ... and is still rejected by the app's check against the full contract
    plan, failure, raw = astra.classify_response(responses_body(call(args)))
    assert plan.status == "error" and not plan.executable
    assert failure.startswith("contract schema")
    assert json.loads(raw) == args


def test_valid_call_becomes_the_plan():
    plan, failure, _ = astra.classify_response(responses_body(call(OK_ARGS)))
    assert failure is None and plan.executable
    assert (plan.target, plan.destination) == ("red_cube", "box_a")


@pytest.mark.parametrize("status", ["unsupported", "needs_review"])
def test_non_ok_status_passes_through_and_is_not_executable(status):
    args = {"status": status, "target": None, "destination": None, "steps": [], "note": "x"}
    plan, failure, _ = astra.classify_response(responses_body(call(args)))
    assert failure is None and plan.status == status and not plan.executable


# --- every failure is an error plan ---------------------------------------------------


REFUSAL = {"type": "message", "content": [{"type": "refusal", "refusal": "I can't help."}]}
TEXT = {"type": "message", "content": [{"type": "output_text", "text": "Here is the plan"}]}


@pytest.mark.parametrize(
    ("resp", "failure_prefix"),
    [
        (
            responses_body(
                call('{"status": "ok", "tar'),
                status="incomplete",
                incomplete_details={"reason": "max_output_tokens"},
            ),
            "incomplete",
        ),
        (responses_body(status="failed", error={"code": "server_error"}), "api error"),
        (responses_body(call(OK_ARGS), status="in_progress"), "status="),
        (responses_body(REFUSAL), "refusal"),
        (responses_body(REFUSAL, call(OK_ARGS)), "refusal"),
        (responses_body(TEXT), "no function_call"),
        (responses_body(), "no function_call"),
        (responses_body(call(OK_ARGS), call(OK_ARGS)), "2 function_calls"),
        (responses_body(call(OK_ARGS, name="run_robot")), "function_call name"),
        (responses_body(call('{"status": "ok"')), "arguments are not"),
        (responses_body(call({"status": "ok"})), "contract schema"),
    ],
    ids=[
        "incomplete",
        "failed",
        "not-completed",
        "refusal",
        "refusal-with-call",
        "text-only",
        "empty-output",
        "two-calls",
        "wrong-tool",
        "truncated-json",
        "schema-invalid",
    ],
)
def test_failure_responses_become_error_plans(resp, failure_prefix):
    plan, failure, _ = astra.classify_response(resp)
    assert plan.status == "error" and not plan.executable and plan.steps == ()
    assert failure.startswith(failure_prefix)


def test_timeout_becomes_error_plan_with_the_astra_timeout():
    post = FakePost(TransportTimeout("no response within 30.0 s"))
    r = astra.structure("x", env=ENV, post=post)
    assert r.plan.status == "error" and not r.plan.executable
    assert r.failure.startswith("timeout") and r.api_status is None
    assert post.calls[0]["timeout"] == S.ASTRA_TIMEOUT_S


def test_transport_error_becomes_error_plan():
    r = astra.structure("x", env=ENV, post=FakePost(TransportError("HTTP 500: boom")))
    assert r.plan.status == "error" and r.failure.startswith("transport")


def test_structure_calls_the_azure_v1_responses_endpoint():
    post = FakePost(responses_body(call(OK_ARGS)))
    r = astra.structure("red cube into the left tray", env=ENV, post=post)
    assert r.ok and r.plan.executable and r.model == "gpt-6-astra"
    sent = post.calls[0]
    assert sent["url"] == "https://example.openai.azure.com/openai/v1/responses"
    assert sent["headers"] == {"api-key": "test-key"}
    assert sent["body"]["input"] == "Sentence: red cube into the left tray"


def test_missing_settings_fail_before_any_request():
    post = FakePost()
    with pytest.raises(RuntimeError, match="AOAI_ASTRA_DEPLOYMENT"):
        astra.structure("x", env={**ENV, "AOAI_ASTRA_DEPLOYMENT": ""}, post=post)
    assert post.calls == []


# --- Cosmos client --------------------------------------------------------------------


def test_cosmos_request_puts_the_video_first_and_uses_the_settings():
    body = cosmos.build_request(VIDEO)
    content = body["messages"][0]["content"]
    assert content[0] == {"type": "video_url", "video_url": {"url": VIDEO.resolve().as_uri()}}
    assert content[1]["type"] == "text" and "one sentence" in content[1]["text"]
    assert "timestamps or coordinates" in content[1]["text"]  # no times, no coordinates
    assert "cannot tell" in content[1]["text"]  # the escape
    assert body["max_tokens"] == S.COSMOS_MAX_TOKENS
    assert body["mm_processor_kwargs"] == {"fps": S.COSMOS_SAMPLE_FPS, "do_sample_frames": True}
    assert "response_format" not in body  # Cosmos never writes JSON (ADR 0003)


def test_cosmos_stop_returns_the_sentence_and_keeps_the_trace():
    post = FakePost(chat_body("A hand puts the red cube into the left tray."))
    r = cosmos.describe(VIDEO, base_url="http://h:8000/", post=post)
    assert r.ok and r.sentence == "A hand puts the red cube into the left tray."
    assert r.finish_reason == "stop" and r.reasoning == "thinking"
    assert post.calls[0]["url"] == "http://h:8000/v1/chat/completions"
    assert post.calls[0]["timeout"] == S.COSMOS_TIMEOUT_S


@pytest.mark.parametrize("finish", ["length", "abort", None])
def test_cosmos_finish_reason_other_than_stop_is_a_failure(finish):
    r = cosmos.describe(VIDEO, post=FakePost(chat_body("the hand moves the hand moves", finish)))
    assert not r.ok and "finish_reason" in r.failure


@pytest.mark.parametrize(
    ("resp", "failure"),
    [
        (TransportTimeout("no response within 30.0 s"), "timeout"),
        (TransportError("connection failed"), "transport"),
        (chat_body(""), "empty final answer"),
        (chat_body("<think>only thinking, never closed"), "empty"),
        ({"choices": []}, "no choices"),
    ],
    ids=["timeout", "transport", "empty", "unclosed-think", "no-choices"],
)
def test_cosmos_failures(resp, failure):
    r = cosmos.describe(VIDEO, post=FakePost(resp))
    assert not r.ok and r.sentence is None and failure in r.failure


def test_cosmos_uses_only_the_first_repeated_answer():
    content = "<think>x</think>\nRed cube into left tray.\n</think>\nRed cube into left tray."
    assert cosmos.final_answer(content) == "Red cube into left tray."
    assert cosmos.final_answer("Red cube.\n</think>\nRed cube.") == "Red cube."


def test_cosmos_unclosed_think_is_not_an_answer():
    assert cosmos.final_answer("<think>only thinking, never closed") == ""


# --- pipeline -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cosmos_resp",
    [chat_body("the hand moves " * 50, "length"), TransportTimeout("t"), chat_body("")],
    ids=["finish-length", "timeout", "empty"],
)
def test_cosmos_failure_stops_before_astra_with_an_error_plan(cosmos_resp):
    post = FakePost(cosmos_resp)  # a second call would fail: no scripted Astra response
    u = pipeline.understand(VIDEO, env=ENV, post=post)
    assert u.failed_stage == "cosmos" and u.astra is None
    assert u.plan.status == "error" and not u.plan.executable
    assert len(post.calls) == 1


def test_pipeline_passes_the_sentence_to_astra():
    post = FakePost(
        chat_body("A hand puts the red cube into the left tray."), responses_body(call(OK_ARGS))
    )
    u = pipeline.understand(VIDEO, env=ENV, post=post)
    assert u.failed_stage is None and u.plan.executable
    assert (
        post.calls[1]["body"]["input"] == "Sentence: A hand puts the red cube into the left tray."
    )


def test_pipeline_astra_failure_is_reported_as_astra():
    post = FakePost(chat_body("A sentence."), responses_body(REFUSAL))
    u = pipeline.understand(VIDEO, env=ENV, post=post)
    assert u.failed_stage == "astra" and u.plan.status == "error"


# --- operational values live in one place ---------------------------------------------


def test_clients_read_the_operational_values_from_settings(monkeypatch):
    monkeypatch.setattr(S, "COSMOS_MAX_TOKENS", 7)
    monkeypatch.setattr(S, "COSMOS_TIMEOUT_S", 1.5)
    monkeypatch.setattr(S, "ASTRA_TIMEOUT_S", 2.5)
    post = FakePost(chat_body("A sentence."), responses_body(call(OK_ARGS)))
    pipeline.understand(VIDEO, env=ENV, post=post)
    assert post.calls[0]["body"]["max_tokens"] == 7
    assert post.calls[0]["timeout"] == 1.5
    assert post.calls[1]["timeout"] == 2.5


def test_no_client_module_hardcodes_a_timeout_or_token_limit():
    src = Path(cosmos.__file__).parent
    for name in ("cosmos.py", "astra.py", "pipeline.py", "transport.py"):
        text = (src / name).read_text(encoding="utf-8")
        for needle in ("timeout=3", "max_tokens=", "TIMEOUT_S =", "MAX_TOKENS ="):
            assert needle not in text, f"{name}: {needle}"


# --- transport ------------------------------------------------------------------------


class _Resp:
    def __init__(self, raw=b"{}", exc=None):
        self.raw, self.exc = raw, exc

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def close(self):
        pass

    def read(self, n=-1):
        if self.exc:
            raise self.exc
        raw, self.raw = self.raw, b""
        return raw


@pytest.mark.parametrize(
    ("opener", "expected"),
    [
        (
            lambda: (_ for _ in ()).throw(http.client.RemoteDisconnected("closed")),
            "connection lost",
        ),
        (lambda: _Resp(exc=ConnectionResetError(104, "reset")), "connection lost"),
        (lambda: _Resp(exc=http.client.IncompleteRead(b"{")), "connection lost"),
        (lambda: _Resp(raw=b"[1, 2]"), "not a JSON object"),
        (lambda: _Resp(raw=b"<html>"), "not JSON"),
    ],
    ids=["remote-disconnected", "reset-on-read", "incomplete-read", "json-list", "html"],
)
def test_post_json_turns_late_network_failures_into_transport_error(monkeypatch, opener, expected):
    monkeypatch.setattr(transport.urllib.request, "urlopen", lambda req, timeout: opener())
    with pytest.raises(TransportError, match=expected):
        transport.post_json("http://h/x", {}, {}, 1.0)


def test_cosmos_connection_lost_mid_inference_is_a_failure_not_a_crash(monkeypatch):
    def urlopen(req, timeout):
        raise http.client.RemoteDisconnected("server closed")

    monkeypatch.setattr(transport.urllib.request, "urlopen", urlopen)
    r = cosmos.describe(VIDEO)
    assert not r.ok and r.failure.startswith("transport")


class _Server:
    """A local HTTP server whose POST handler is `handle(handler)`; stopped on exit."""

    def __init__(self, handle):
        stop = self.stop = threading.Event()

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                handle(self, stop)

            def log_message(self, *a):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.httpd.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/x"

    def __enter__(self):
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *a):
        self.stop.set()
        self.httpd.shutdown()
        self.httpd.server_close()


def _trickle(status, body, *, gap=0.03, stop_after=None):
    def handle(h, stop):
        h.send_response(status)
        h.send_header("Content-Type", "application/json")
        h.send_header("Content-Length", str(len(body)))
        h.end_headers()
        for i, byte in enumerate(body):
            if stop.is_set() or (stop_after is not None and i >= stop_after):
                return  # connection closes with the body incomplete
            h.wfile.write(bytes([byte]))
            h.wfile.flush()
            stop.wait(gap)

    return handle


def test_post_json_timeout_is_a_deadline_for_the_whole_body():
    # every byte arrives well within the per-operation timeout, the whole body does not
    body = json.dumps({"status": "completed", "pad": "x" * 60}).encode()
    with _Server(_trickle(200, body)) as srv:
        t0 = time.monotonic()
        with pytest.raises(TransportTimeout):
            transport.post_json(srv.url, {}, {}, 0.3)
        assert time.monotonic() - t0 < 0.6


def test_post_json_same_trickle_succeeds_when_it_fits_the_deadline():
    body = json.dumps({"ok": True}).encode()
    with _Server(_trickle(200, body, gap=0.005)) as srv:
        assert transport.post_json(srv.url, {}, {}, 5.0) == {"ok": True}


def test_post_json_error_body_that_stalls_is_a_timeout():
    body = b'{"error": "' + b"e" * 80 + b'"}'
    with _Server(_trickle(500, body, gap=0.05)) as srv:
        with pytest.raises(TransportTimeout):
            transport.post_json(srv.url, {}, {}, 0.3)


def test_post_json_error_body_cut_short_keeps_the_status():
    body = b'{"error": "' + b"e" * 80 + b'"}'
    with _Server(_trickle(500, body, gap=0.0, stop_after=5)) as srv:
        with pytest.raises(TransportError, match="HTTP 500: \\(body unreadable"):
            transport.post_json(srv.url, {}, {}, 2.0)


def test_post_json_error_body_read_failure_is_a_transport_error(monkeypatch):
    def urlopen(req, timeout):
        fp = _Resp(exc=ConnectionResetError(104, "reset"))
        raise urllib.error.HTTPError("http://h/x", 503, "busy", {}, fp)

    monkeypatch.setattr(transport.urllib.request, "urlopen", urlopen)
    with pytest.raises(TransportError, match="HTTP 503"):
        transport.post_json("http://h/x", {}, {}, 1.0)


# --- responses of the wrong shape ------------------------------------------------------


@pytest.mark.parametrize(
    "resp",
    [
        {"status": "completed", "output": [None]},
        {"status": "completed", "output": "text"},
        {"status": "completed", "output": {"type": "function_call"}},
        {"status": "completed", "output": [{"type": "message", "content": [None]}]},
        {"status": "completed", "output": [{"type": "message", "content": "hi"}]},
        {"status": "incomplete", "incomplete_details": "max_output_tokens"},
        {
            "status": "completed",
            "output": [{"type": "function_call", "name": "submit_plan", "arguments": {"a": 1}}],
        },
        {"status": ["completed"], "output": []},
    ],
    ids=[
        "output-null-item",
        "output-str",
        "output-dict",
        "content-null-part",
        "content-str",
        "incomplete-details-str",
        "arguments-dict",
        "status-list",
    ],
)
def test_astra_response_of_the_wrong_shape_is_an_error_plan(resp):
    plan, failure, _ = astra.classify_response(resp)
    assert plan.status == "error" and not plan.executable and failure


def test_astra_structure_survives_wrong_types_in_metadata():
    resp = {"status": "completed", "output": [None], "id": 5, "model": [], "usage": "x"}
    r = astra.structure("s", env=ENV, post=FakePost(resp))
    assert r.plan.status == "error" and r.response_id is None and r.usage == {}


@pytest.mark.parametrize(
    "resp",
    [
        {"choices": [None]},
        {"choices": "x"},
        {"choices": [{"finish_reason": "stop", "message": "hello"}]},
        {"choices": [{"finish_reason": "stop", "message": {"content": {"text": "hello"}}}]},
        {"choices": [{"finish_reason": "stop", "message": {"content": ["hello"]}}]},
    ],
    ids=["choice-null", "choices-str", "message-str", "content-dict", "content-list"],
)
def test_cosmos_response_of_the_wrong_shape_is_a_failure(resp):
    r = cosmos.describe(VIDEO, post=FakePost(resp))
    assert not r.ok and r.sentence is None
