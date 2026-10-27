"""Start the approval screen: `uv run python -m so101_demo.ui [--render] [--port 8765]`.

Opens nothing by itself; open http://localhost:<port>/ (approval) and /big (big screen)
in the Windows browser, one full-screen window each (docs/approval-screen.md).
"""

from __future__ import annotations

import argparse
import os

from ..evidence.gl_env import prefer_gpu_on_wsl2


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--render", action="store_true", help="render the sim for /big")
    ap.add_argument("--delay", type=float, default=1.0, help="fixed-JSON reading delay (s)")
    args = ap.parse_args(argv)
    if args.render:
        # offscreen EGL, not GLX through WSLg's X server; before mujoco is imported
        os.environ.setdefault("MUJOCO_GL", "egl")
        prefer_gpu_on_wsl2()

    import uvicorn

    from ..execution.runner import PlanRunner
    from .frames import FrameStore, renderer_observer
    from .runner_thread import RunnerThread
    from .screen import ApprovalScreen
    from .server import create_app
    from .sources import FixedSource

    frames = FrameStore() if args.render else None
    # styled: the screen look, which also sizes the offscreen buffer for 1280x720 and up
    runner = RunnerThread(
        lambda: PlanRunner(
            observer=renderer_observer(frames) if frames else None, styled=frames is not None
        )
    )
    screen = ApprovalScreen(executor=runner.submit)
    app = create_app(screen, FixedSource(delay_s=args.delay), frames)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
