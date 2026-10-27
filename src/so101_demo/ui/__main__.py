"""Start the approval screen: `uv run python -m so101_demo.ui [--render] [--port 8765]`.

Opens nothing by itself; open http://localhost:<port>/ (approval) and /big (big screen)
in the Windows browser, one full-screen window each (docs/approval-screen.md).

    --videos <manifest|dir>   real videos: the approval screen lists them, the selected one
                              plays on the big screen and goes through Cosmos and Astra
                              (needs the Cosmos server and the Astra keys in .env);
                              default: fixed JSONs
    --ids a,b,c               which videos of --videos, in this order (default: all)
    --record <dir>            record every run as <dir>/<run_id>-<target>-<destination>.mp4
                              (1920x1080, 25 fps) and write <dir>/runs.jsonl; implies --render
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from ..evidence.gl_env import gpu_name, prefer_gpu_on_wsl2


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--render", action="store_true", help="render the sim for /big")
    ap.add_argument("--delay", type=float, default=1.0, help="fixed-JSON reading delay (s)")
    ap.add_argument("--videos", type=Path, help="video manifest (videos.json) or directory")
    ap.add_argument("--ids", help="comma-separated ids of --videos to use, in this order")
    ap.add_argument("--record", type=Path, help="record runs and runs.jsonl here")
    args = ap.parse_args(argv)
    if args.ids and args.videos is None:
        ap.error("--ids needs --videos")
    render = args.render or args.record is not None
    if render:
        # offscreen EGL, not GLX through WSLg's X server; before mujoco is imported
        os.environ.setdefault("MUJOCO_GL", "egl")
        prefer_gpu_on_wsl2()

    import uvicorn

    from ..execution.runner import PlanRunner
    from .frames import FrameStore
    from .recording import ScreenRecorder
    from .runner_thread import RunnerThread
    from .screen import ApprovalScreen
    from .server import create_app
    from .sources import FixedSource, VideoSource, load_videos

    ids = [i for i in (args.ids or "").split(",") if i]
    if args.videos is not None:
        videos = load_videos(args.videos, ids or None)
        source = VideoSource(videos)
        print(f"videos: {', '.join(v.id for v in videos)}")
    else:
        source = FixedSource(delay_s=args.delay)
    frames = FrameStore() if render else None
    # styled: the screen look, which also sizes the offscreen buffer for 1920x1080
    recorder = ScreenRecorder(frames, args.record) if frames is not None else None
    rt = RunnerThread(
        lambda: PlanRunner(
            observer=recorder.observer if recorder is not None else None,
            styled=recorder is not None,
        )
    )
    executor = (lambda a: rt.run(lambda r: recorder.run(r, a))) if recorder else rt.submit
    log = None
    if args.record is not None:
        from .evidence_e2e import session_info
        from .runs_log import RunsLog

        gpu = gpu_name()
        log = RunsLog(
            args.record / "runs.jsonl",
            source=source if isinstance(source, VideoSource) else None,
            recordings=recorder.recordings,
            session=session_info(
                videos=args.videos,
                ids=ids,
                machine=lambda: {"gpu": gpu, "gl": recorder.gl_name, "encoder": recorder.encoder},
            ),
        )
        executor = log.executor(executor)
        print(f"recording to {args.record} (runs.jsonl + mp4 per run)")
    video_files = source.files if isinstance(source, VideoSource) else {}
    screen = ApprovalScreen(executor=executor, on_log=log, videos=list(video_files))
    app = create_app(screen, source, frames, video_files)
    print(f"approval screen: http://localhost:{args.port}/  big screen: /big")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
