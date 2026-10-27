"""Visual-only styling of the stage-1 scene for the display screen (2026-09-19).

Everything here touches `mjVisual`, materials, lights and textures: nothing that
`mj_step` reads. `tests/test_appearance.py` checks that a styled and an unstyled
scene have identical physics arrays and identical trajectories. The defaults come
from the appearance rules: plain table and background, one fixed camera,
enough light and a shadow to read the lift height, 1080p offscreen buffer.
"""

from __future__ import annotations

import mujoco

OFFSCREEN_WIDTH, OFFSCREEN_HEIGHT = 1920, 1080
SHADOW_SIZE = 4096
# plain, matte and slightly cooler than the table so the horizon reads as depth
BACKGROUND_RGB = (0.80, 0.84, 0.88)
# key light: above and in front-left of the work area, casting the only shadow
KEY_LIGHT_POS = (0.35, -0.20, 0.85)
KEY_LIGHT_TARGET = (0.19, 0.0, 0.0)
KEY_LIGHT_DIFFUSE = (0.45, 0.45, 0.45)
KEY_LIGHT_SPECULAR = (0.15, 0.15, 0.15)
KEY_LIGHT_AMBIENT = (0.05, 0.05, 0.05)
HEADLIGHT_AMBIENT = (0.38, 0.38, 0.38)
HEADLIGHT_DIFFUSE = (0.42, 0.42, 0.42)
HEADLIGHT_SPECULAR = (0.10, 0.10, 0.10)
SKYBOX_TEXTURE = "scene_sky"
KEY_LIGHT_NAME = "scene_key"


def style_spec(spec: mujoco.MjSpec) -> None:
    """Apply the screen styling to a scene spec before it is compiled."""
    g = spec.visual.global_
    g.offwidth, g.offheight = OFFSCREEN_WIDTH, OFFSCREEN_HEIGHT
    spec.visual.quality.shadowsize = SHADOW_SIZE
    h = spec.visual.headlight
    h.ambient[:] = HEADLIGHT_AMBIENT
    h.diffuse[:] = HEADLIGHT_DIFFUSE
    h.specular[:] = HEADLIGHT_SPECULAR

    sky = spec.add_texture()
    sky.name = SKYBOX_TEXTURE
    sky.type = mujoco.mjtTexture.mjTEXTURE_SKYBOX
    sky.builtin = mujoco.mjtBuiltin.mjBUILTIN_FLAT
    sky.rgb1[:] = BACKGROUND_RGB
    sky.rgb2[:] = BACKGROUND_RGB
    sky.width = sky.height = 32

    light = spec.worldbody.add_light()
    light.name = KEY_LIGHT_NAME
    light.type = mujoco.mjtLightType.mjLIGHT_SPOT
    light.pos[:] = KEY_LIGHT_POS
    d = [t - p for t, p in zip(KEY_LIGHT_TARGET, KEY_LIGHT_POS, strict=True)]
    n = sum(x * x for x in d) ** 0.5
    light.dir[:] = [x / n for x in d]
    light.cutoff = 70.0
    light.castshadow = True
    light.diffuse[:] = KEY_LIGHT_DIFFUSE
    light.specular[:] = KEY_LIGHT_SPECULAR
    light.ambient[:] = KEY_LIGHT_AMBIENT
