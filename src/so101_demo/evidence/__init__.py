"""Evidence generators (docs/evidence/*). Selecting the GPU for rendering has to happen
before MuJoCo is imported anywhere in the process, so it is done here."""

from .gl_env import prefer_gpu_on_wsl2

prefer_gpu_on_wsl2()
