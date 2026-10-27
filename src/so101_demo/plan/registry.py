"""Registered objects, destinations and the fixed 0b trial manifest (contracts/registry.json)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from .paths import REGISTRY_PATH


@dataclass(frozen=True)
class ObjectSpec:
    id: str
    size_m: float
    mass_kg: float
    color: str
    rgba: tuple[float, float, float, float]
    label: str
    home_xyz_m: tuple[float, float, float]
    yaw_deg: float


@dataclass(frozen=True)
class DestinationSpec:
    id: str
    inner_m: float
    wall_thickness_m: float
    wall_height_m: float
    center_xy_m: tuple[float, float]
    color: str
    rgba: tuple[float, float, float, float]
    label: str
    # Trays are built axis-aligned (sim.scene); the place yaw follows this value so the
    # cube's edges end up parallel to the walls (Issue #21). Not in registry.json.
    yaw_deg: float = 0.0

    @property
    def outer_half_m(self) -> float:
        return self.inner_m / 2 + self.wall_thickness_m


@dataclass(frozen=True)
class TrialSpec:
    trial: str
    object: str
    start_xy_m: tuple[float, float]
    destination: str


@dataclass(frozen=True)
class Registry:
    table_z_m: float
    objects: dict[str, ObjectSpec]
    destinations: dict[str, DestinationSpec]
    trials_0b: tuple[TrialSpec, ...]

    def object_rest_z(self, object_id: str) -> float:
        return self.table_z_m + self.objects[object_id].size_m / 2

    def start_xyz(self, trial: TrialSpec) -> np.ndarray:
        x, y = trial.start_xy_m
        return np.array([x, y, self.object_rest_z(trial.object)])


@lru_cache(maxsize=1)
def load_registry() -> Registry:
    raw = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    objects = {
        k: ObjectSpec(
            k,
            v["size_m"],
            v["mass_kg"],
            v["color"],
            tuple(v["rgba"]),
            v["label"],
            tuple(v["home_xyz_m"]),
            v["yaw_deg"],
        )
        for k, v in raw["objects"].items()
    }
    destinations = {
        k: DestinationSpec(
            k,
            v["inner_m"],
            v["wall_thickness_m"],
            v["wall_height_m"],
            tuple(v["center_xy_m"]),
            v["color"],
            tuple(v["rgba"]),
            v["label"],
        )
        for k, v in raw["destinations"].items()
    }
    trials = tuple(
        TrialSpec(t["trial"], t["object"], tuple(t["start_xy_m"]), t["destination"])
        for t in raw["trials_0b"]
    )
    return Registry(raw["table_z_m"], objects, destinations, trials)
