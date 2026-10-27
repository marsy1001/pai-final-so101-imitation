"""Pick the GPU for offscreen rendering on WSL2 (Issue #10, measured 2026-09-19).

On WSL2 Mesa's EGL falls back to llvmpipe (CPU) unless the D3D12 Gallium driver is
selected, and that driver picks the first adapter (the Intel iGPU on the GPU
machine) unless told otherwise. Measured at 1920x1080 with the styled scene:
llvmpipe 94 ms/frame, D3D12 Intel UHD 23 ms, D3D12 RTX 4090 Laptop 15 ms.
Must run before `import mujoco` (the EGL display is created at import time).
Imports nothing from MuJoCo on purpose.
"""

from __future__ import annotations

import os

WSL2_DXG_DEVICE = "/dev/dxg"


def prefer_gpu_on_wsl2() -> dict[str, str]:
    """Set the Mesa selectors if unset and on WSL2; returns what is in effect."""
    if os.path.exists(WSL2_DXG_DEVICE):
        os.environ.setdefault("GALLIUM_DRIVER", "d3d12")
        os.environ.setdefault("MESA_D3D12_DEFAULT_ADAPTER_NAME", "NVIDIA")
    return {
        k: os.environ.get(k, "")
        for k in ("MUJOCO_GL", "GALLIUM_DRIVER", "MESA_D3D12_DEFAULT_ADAPTER_NAME")
    }


def gl_renderer_name() -> str:
    """GL_RENDERER of the current context (call after a mujoco.Renderer exists)."""
    try:
        from OpenGL import GL

        s = GL.glGetString(GL.GL_RENDERER)
        return s.decode() if isinstance(s, bytes) else str(s)
    except Exception as e:  # noqa: BLE001 - evidence only
        return f"unknown ({type(e).__name__})"
