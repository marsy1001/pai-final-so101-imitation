"""Stage-1 scene: the SO-101 model, a table plane, the registered cubes and trays.

Built with MjSpec from the pinned MJCF and contracts/registry.json, so the scene and
the plan schema share one source of ids and geometry. Every cube gets an inactive
weld to the gripper body; `sim.grasp` toggles it under the simplified-grasp rules.
Trays are static bodies made of four wall boxes (no floor: the table is the floor).
"""

from __future__ import annotations

from dataclasses import dataclass

import mujoco
import numpy as np

from ..assets import mjcf_path
from ..plan import Registry, load_registry
from .appearance import style_spec
from .model import So101Sim

GRIPPER_BODY = "gripper"
TABLE_GEOM = "table"


def weld_name(object_id: str) -> str:
    return f"weld_{object_id}"


def cube_geom_name(object_id: str) -> str:
    return f"{object_id}_geom"


CONFIG_IDS = ("sim-v1-simple", "sim-v1-contact")


@dataclass(frozen=True)
class SceneConfig:
    """Which registered things are in the scene. `config_id` names the physics/grasp
    configuration in the evidence: `sim-v1-simple` = simplified grasp (welds toggled
    by sim.grasp), `sim-v1-contact` = real contact, no welds in the model at all."""

    config_id: str
    objects: tuple[str, ...]
    destinations: tuple[str, ...] = ("box_a", "box_b")

    def __post_init__(self) -> None:
        if self.config_id not in CONFIG_IDS:
            raise ValueError(f"unknown config_id {self.config_id!r}; known: {CONFIG_IDS}")

    @property
    def simplified_grasp(self) -> bool:
        return self.config_id == "sim-v1-simple"


def build_scene(
    config: SceneConfig, registry: Registry | None = None, *, styled: bool = False
) -> mujoco.MjModel:
    """`styled=True` adds the screen look (sim.appearance): visual elements only."""
    reg = registry or load_registry()
    spec = mujoco.MjSpec.from_file(str(mjcf_path()))
    if styled:
        style_spec(spec)
    world = spec.worldbody
    world.add_geom(
        name=TABLE_GEOM,
        type=mujoco.mjtGeom.mjGEOM_PLANE,
        size=[1.0, 1.0, 0.05],
        pos=[0.0, 0.0, reg.table_z_m],
        rgba=[0.93, 0.93, 0.90, 1.0],
    )
    for oid in config.objects:
        o = reg.objects[oid]
        body = world.add_body(name=oid, pos=list(o.home_xyz_m))
        body.add_freejoint(name=f"{oid}_free")
        body.add_geom(
            name=cube_geom_name(oid),
            type=mujoco.mjtGeom.mjGEOM_BOX,
            size=[o.size_m / 2] * 3,
            rgba=list(o.rgba),
            mass=o.mass_kg,
        )
        if config.simplified_grasp:
            spec.add_equality(
                name=weld_name(oid),
                type=mujoco.mjtEq.mjEQ_WELD,
                objtype=mujoco.mjtObj.mjOBJ_BODY,
                name1=GRIPPER_BODY,
                name2=oid,
                active=False,
                # stiff enough that a 20 g cube does not sag while carried
                solref=[0.005, 1.0],
                solimp=[0.99, 0.99, 0.001, 0.5, 2.0],
            )
    for did in config.destinations:
        d = reg.destinations[did]
        cx, cy = d.center_xy_m
        body = world.add_body(name=did, pos=[cx, cy, reg.table_z_m])
        half_in, t, h = d.inner_m / 2, d.wall_thickness_m, d.wall_height_m
        off = half_in + t / 2
        walls = [
            ("x_pos", [t / 2, half_in + t, h / 2], [off, 0.0, h / 2]),
            ("x_neg", [t / 2, half_in + t, h / 2], [-off, 0.0, h / 2]),
            ("y_pos", [half_in, t / 2, h / 2], [0.0, off, h / 2]),
            ("y_neg", [half_in, t / 2, h / 2], [0.0, -off, h / 2]),
        ]
        for tag, size, pos in walls:
            body.add_geom(
                name=f"{did}_wall_{tag}",
                type=mujoco.mjtGeom.mjGEOM_BOX,
                size=size,
                pos=pos,
                rgba=list(d.rgba),
            )
    return spec.compile()


def load_scene(
    config: SceneConfig, registry: Registry | None = None, *, styled: bool = False
) -> So101Sim:
    return So101Sim(model=build_scene(config, registry, styled=styled))


def free_joint_qpos_adr(sim: So101Sim, object_id: str) -> int:
    jid = mujoco.mj_name2id(sim.model, mujoco.mjtObj.mjOBJ_JOINT, f"{object_id}_free")
    if jid < 0:
        raise ValueError(f"{object_id} is not in the scene")
    return int(sim.model.jnt_qposadr[jid])


def set_object_pose(sim: So101Sim, object_id: str, xyz, yaw_deg: float = 0.0) -> None:
    """Place a free body (position + yaw) and zero its velocity. Used only by reset."""
    adr = free_joint_qpos_adr(sim, object_id)
    half = np.radians(yaw_deg) / 2
    sim.data.qpos[adr : adr + 3] = np.asarray(xyz, dtype=float)
    sim.data.qpos[adr + 3 : adr + 7] = [np.cos(half), 0.0, 0.0, np.sin(half)]
    dof = int(
        sim.model.jnt_dofadr[
            mujoco.mj_name2id(sim.model, mujoco.mjtObj.mjOBJ_JOINT, f"{object_id}_free")
        ]
    )
    sim.data.qvel[dof : dof + 6] = 0.0
    mujoco.mj_forward(sim.model, sim.data)


def penetrations(sim: So101Sim, min_depth_m: float = 1e-4) -> list[tuple[str, str, float]]:
    """Contacts deeper than `min_depth_m` after mj_forward: (geom_a, geom_b, depth)."""
    mujoco.mj_forward(sim.model, sim.data)
    out = []
    for i in range(sim.data.ncon):
        c = sim.data.contact[i]
        if c.dist < -min_depth_m:
            a = mujoco.mj_id2name(sim.model, mujoco.mjtObj.mjOBJ_GEOM, c.geom1) or str(c.geom1)
            b = mujoco.mj_id2name(sim.model, mujoco.mjtObj.mjOBJ_GEOM, c.geom2) or str(c.geom2)
            out.append((a, b, float(-c.dist)))
    return out
