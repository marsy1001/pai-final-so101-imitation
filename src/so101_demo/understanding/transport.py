"""JSON over HTTP POST with the standard library, and the .env reader.

Clients take a `Post` callable so tests replace the network with a fake; the real one is
`post_json`. A timeout and every other transport failure are separate exception types so
the caller can record which one happened. The timeout is a deadline for the whole
exchange (see `post_json`).
"""

from __future__ import annotations

import http.client
import json
import os
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from ..plan.paths import REPO_ROOT

Post = Callable[[str, dict[str, str], dict[str, Any], float], dict[str, Any]]


class TransportTimeout(Exception):
    """No complete response within the timeout."""


class TransportError(Exception):
    """HTTP error status, connection failure or a body that is not JSON."""


_CHUNK = 65536


def post_json(url: str, headers: dict[str, str], body: dict[str, Any], timeout: float) -> dict:
    """POST `body` as JSON and return the JSON object in the response.

    `timeout` is a deadline for the whole exchange (connect, send, headers and the full
    body), not the per-socket-operation timeout of `urlopen`: a server that trickles bytes
    would otherwise keep a request alive indefinitely. The exchange runs in a worker thread
    that also stops reading at the deadline; a response that completes after the deadline
    is discarded and the call raises `TransportTimeout`.
    """
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", **headers},
    )
    deadline = time.monotonic() + timeout
    outcome: dict[str, Any] = {}

    def work() -> None:
        try:
            outcome["raw"] = _exchange(req, timeout, deadline)
        except (TransportTimeout, TransportError) as e:
            outcome["error"] = e

    worker = threading.Thread(target=work, name="post_json", daemon=True)
    worker.start()
    worker.join(max(0.0, deadline - time.monotonic()))
    if worker.is_alive() or time.monotonic() > deadline:
        raise TransportTimeout(f"no complete response within {timeout} s")
    if "error" in outcome:
        raise outcome["error"]
    raw = outcome["raw"]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise TransportError(f"response is not JSON: {raw[:200]!r}") from e
    if not isinstance(data, dict):
        raise TransportError(f"response is not a JSON object: {raw[:200]!r}")
    return data


def _exchange(req: urllib.request.Request, timeout: float, deadline: float) -> bytes:
    """One request, every failure converted to TransportTimeout / TransportError."""
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            chunks = []
            while chunk := r.read(_CHUNK):
                chunks.append(chunk)
                if time.monotonic() > deadline:
                    raise TimeoutError
            return b"".join(chunks)
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "replace")[:500]
        except (http.client.HTTPException, OSError) as read_error:
            # the status is known; the body is only for the log
            detail = f"(body unreadable: {read_error!r})"
        raise TransportError(f"HTTP {e.code}: {detail}") from e
    except TimeoutError as e:
        raise TransportTimeout(f"no complete response within {timeout} s") from e
    except urllib.error.URLError as e:
        if isinstance(e.reason, TimeoutError):
            raise TransportTimeout(f"no complete response within {timeout} s") from e
        raise TransportError(f"connection failed: {e.reason}") from e
    except (http.client.HTTPException, OSError) as e:
        # urlopen wraps socket errors in URLError only while sending; a server that
        # drops the connection afterwards raises RemoteDisconnected / IncompleteRead /
        # ConnectionResetError unwrapped.
        raise TransportError(f"connection lost: {e!r}") from e


def load_env() -> dict[str, str]:
    """Process environment, then .env in the repository root for keys not set there.

    Values are never printed or logged. Agents cannot read .env themselves; scripts do.
    """
    env = dict(os.environ)
    path = REPO_ROOT / ".env"
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return env
