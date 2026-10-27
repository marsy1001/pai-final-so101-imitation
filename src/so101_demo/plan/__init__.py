"""Loader and validator for the repository-level contract (contracts/plan.schema.json).

The schema and the registry are the canonical files at the repository root; nothing
here copies them. `steps` in the ok case is the only thing the execution layer runs.
"""

from .manifest import G1Manifest, G1Trial, check_manifest, load_g1_manifest, start_violations
from .registry import DestinationSpec, ObjectSpec, Registry, TrialSpec, load_registry
from .schema import Plan, PlanInvalid, load_schema, validate_plan

__all__ = [
    "DestinationSpec",
    "G1Manifest",
    "G1Trial",
    "ObjectSpec",
    "Plan",
    "PlanInvalid",
    "Registry",
    "TrialSpec",
    "check_manifest",
    "load_g1_manifest",
    "start_violations",
    "load_registry",
    "load_schema",
    "validate_plan",
]
