"""Labels on the recorded frames: object and tray names drawn where they are.

Each registered object and destination gets a text label anchored to its projected
3D position (objects follow their body while carried, trays sit still). The label is
white text on a translucent dark box with a bar in the entity's colour, so the
correspondence colour <-> name does not rely on colour alone (the
appearance rules). Text is Latin: the GPU machine has no CJK font and no sudo
(2026-09-19); see docs/appearance.md.

`project` maps a world point to pixel coordinates from the two `mjvGLCamera`s of the
rendered scene (averaged: MuJoCo renders mono as the mean of the stereo pair), so the
labels stay attached under any free-camera setting. `tests/test_appearance*.py`
check it against the image centre and against segmentation centroids.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import mujoco
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ..plan import Registry
from ..sim.model import So101Sim

FONT_PATH = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
FONT_PX = 48  # fixed 2026-09-19; glyph height on a 1080p capture must be >= 32 px
MIN_GLYPH_PX = 32
LABEL_TEXT = {
    "red_cube": "Red cube",
    "blue_cube": "Blue cube",
    "box_a": "Tray A",
    "box_b": "Tray B",
}
OBJECT_LABEL_RISE_PX = 120  # label bottom clears the cube top (cube ~90 px tall at 1080p)
TRAY_LABEL_DROP_PX = 28  # tray label sits below the projected centre (cube goes above)
PAD_PX = 10
BAR_PX = 12
BOX_FILL = (15, 15, 20, 175)
TEXT_FILL = (255, 255, 255, 255)


def project(scene: mujoco.MjvScene, width: int, height: int, xyz) -> tuple[float, float] | None:
    """World point -> (u, v) pixels for the scene's current camera; None if behind it."""
    c0, c1 = scene.camera[0], scene.camera[1]
    pos = (np.asarray(c0.pos) + np.asarray(c1.pos)) / 2
    fwd = np.asarray(c0.forward)
    up = np.asarray(c0.up)
    right = np.cross(fwd, up)
    rel = np.asarray(xyz, dtype=float) - pos
    z = float(rel @ fwd)
    if z <= 1e-6:
        return None
    x, y = float(rel @ right), float(rel @ up)
    tan_half_v = (c0.frustum_top - c0.frustum_bottom) / 2 / c0.frustum_near
    center_shift = (c0.frustum_center + c1.frustum_center) / 2 / c0.frustum_near
    px_per_unit = (height / 2) / tan_half_v
    u = width / 2 + (x / z - center_shift) * px_per_unit
    v = height / 2 - (y / z) * px_per_unit
    return u, v


def font(px: int = FONT_PX) -> ImageFont.FreeTypeFont:
    if not FONT_PATH.exists():
        raise RuntimeError(f"label font not found: {FONT_PATH}")
    return ImageFont.truetype(str(FONT_PATH), px)


@dataclass
class LabelBox:
    """Where a label's text was drawn on one frame (pixel box of the text glyphs).
    `plate` is the box the text sits on (fill + colour bar), which is what has to fit
    inside the frame."""

    left: int
    top: int
    right: int
    bottom: int
    plate: tuple[int, int, int, int] | None = None


@dataclass
class LabelOverlay:
    """`decorate` hook for VideoRecorder: draws the labels, remembers their boxes per
    frame index so the glyph height can be measured on the exported captures."""

    registry: Registry
    width: int
    height: int
    font_px: int = FONT_PX
    boxes_by_frame: dict[int, dict[str, LabelBox]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._font = font(self.font_px)

    def __call__(self, img: np.ndarray, sim: So101Sim, rec) -> np.ndarray:
        pil = Image.fromarray(img)
        draw = ImageDraw.Draw(pil, "RGBA")  # blended fills straight onto the RGB frame
        scene = rec.renderer.scene
        wanted: list[tuple[str, str, tuple, float, float, bool]] = []
        for oid, o in self.registry.objects.items():
            p = project(scene, self.width, self.height, sim.data.body(oid).xpos)
            if p is not None:
                wanted.append(
                    (oid, LABEL_TEXT[oid], o.rgba, p[0], p[1] - OBJECT_LABEL_RISE_PX, True)
                )
        for did, d in self.registry.destinations.items():
            cx, cy = d.center_xy_m
            p = project(scene, self.width, self.height, (cx, cy, d.wall_height_m))
            if p is not None:
                wanted.append(
                    (did, LABEL_TEXT[did], d.rgba, p[0], p[1] + TRAY_LABEL_DROP_PX, False)
                )
        rects = {k: self._rect(draw, text, u, v) for k, text, _, u, v, _ in wanted}
        self._separate(rects, {k: up for k, *_, up in wanted})
        boxes: dict[str, LabelBox] = {}
        for k, text, rgba, *_ in wanted:
            boxes[k] = self._draw(draw, text, rgba, rects[k])
        self.boxes_by_frame[rec.frames] = boxes  # rec.frames = index of the frame being made
        return np.asarray(pil)

    def _rect(self, draw: ImageDraw.ImageDraw, text: str, u: float, v_top: float) -> list[int]:
        bl, t, r, b = draw.textbbox((0, 0), text, font=self._font)
        w, h = r - bl, b - t
        x0 = int(round(u - w / 2)) - PAD_PX
        y0 = int(round(v_top))
        return [x0, y0, x0 + w + 2 * PAD_PX + BAR_PX, y0 + h + 2 * PAD_PX]

    @staticmethod
    def _separate(rects: dict[str, list[int]], goes_up: dict[str, bool], gap: int = 6) -> None:
        """Push overlapping label boxes apart vertically (objects up, trays down) so no
        label hides another; a few passes settle four labels."""
        keys = list(rects)
        for _ in range(8):
            moved = False
            for i, a in enumerate(keys):
                for b in keys[i + 1 :]:
                    ra, rb = rects[a], rects[b]
                    if ra[2] <= rb[0] or rb[2] <= ra[0] or ra[3] <= rb[1] or rb[3] <= ra[1]:
                        continue
                    # move the one that is allowed to go in its own direction
                    mover, other = (a, b) if goes_up[a] or not goes_up[b] else (b, a)
                    rm, ro = rects[mover], rects[other]
                    if goes_up[mover]:
                        dy = -(rm[3] - ro[1] + gap)
                    else:
                        dy = ro[3] - rm[1] + gap
                    rm[1] += dy
                    rm[3] += dy
                    moved = True
            if not moved:
                return

    def _draw(self, draw: ImageDraw.ImageDraw, text: str, rgba, rect: list[int]) -> LabelBox:
        bl, t, r, b = draw.textbbox((0, 0), text, font=self._font)
        x0, y0, x1, y1 = rect
        draw.rectangle([x0, y0, x1, y1], fill=BOX_FILL)
        colour = tuple(int(255 * c) for c in rgba[:3]) + (255,)
        draw.rectangle([x0, y0, x0 + BAR_PX, y1], fill=colour)
        tx, ty = x0 + BAR_PX + PAD_PX - bl, y0 + PAD_PX - t
        draw.text((tx, ty), text, font=self._font, fill=TEXT_FILL)
        return LabelBox(tx + bl, ty + t, tx + r, ty + b, (x0, y0, x1, y1))


def margin_px(box: LabelBox, width: int, height: int) -> int:
    """Smallest distance from the drawn label plate to the frame edge. 0 means the label
    touches the edge, a negative value that it is clipped by it."""
    left, top, right, bottom = box.plate or (box.left, box.top, box.right, box.bottom)
    return int(min(left, top, width - 1 - right, height - 1 - bottom))


def glyph_height_px(img: np.ndarray, box: LabelBox, white: int = 230) -> int:
    """Rows inside `box` that contain text pixels (near-white on the dark label box).
    This is the on-screen measurement, taken from the exported
    capture, not from the font metric."""
    crop = img[box.top : box.bottom + 1, box.left : box.right + 1]
    ink = (crop >= white).all(axis=2)
    rows = np.flatnonzero(ink.any(axis=1))
    return int(rows[-1] - rows[0] + 1) if rows.size else 0
