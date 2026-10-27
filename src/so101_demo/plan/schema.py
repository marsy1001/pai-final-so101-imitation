"""Validate a plan against contracts/plan.schema.json and expose it as a typed object."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from jsonschema import Draft202012Validator

from .paths import PLAN_SCHEMA_PATH


class PlanInvalid(Exception):
    """The JSON does not satisfy the contract. Never turned into an executable plan."""


@dataclass(frozen=True)
class Step:
    action: str
    object: str | None = None
    destination: str | None = None


@dataclass(frozen=True)
class Plan:
    status: str
    target: str | None
    destination: str | None
    steps: tuple[Step, ...]
    note: str

    @property
    def executable(self) -> bool:
        return self.status == "ok"


@lru_cache(maxsize=1)
def load_schema() -> dict[str, Any]:
    return json.loads(PLAN_SCHEMA_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    schema = load_schema()
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def validate_plan(data: Any) -> Plan:
    """Return a `Plan` or raise `PlanInvalid` listing every schema violation."""
    errors = sorted(_validator().iter_errors(data), key=lambda e: list(e.path))
    if errors:
        raise PlanInvalid(
            "; ".join(f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in errors)
        )
    steps = tuple(
        Step(action=s["action"], object=s.get("object"), destination=s.get("destination"))
        for s in data["steps"]
    )
    plan = Plan(data["status"], data["target"], data["destination"], steps, data["note"])
    # The schema's ok branch already enforces this (enumerated combinations); the
    # restatement is a guard against a schema edit that drops it.
    if plan.executable and (
        steps[0].object != plan.target or steps[1].destination != plan.destination
    ):
        raise PlanInvalid("steps do not match target/destination")
    return plan
