"""Print what Cosmos' video processor does to one video, as JSON (Issue #22).

    uv run --project serving/cosmos python serving/cosmos/probe_video.py <video> [fps]

Runs the model's own processor (Qwen3VLVideoProcessor, from the cached weights' config)
with the same `fps` / `do_sample_frames` the client sends in `mm_processor_kwargs`, so
the numbers are the ones the server sees: sampled frames, the per-frame size after the
total-pixel budget (`size.longest_edge`) and the number of video tokens.
"""

import json
import sys

import cv2
import numpy as np
from transformers import AutoVideoProcessor
from transformers.video_utils import VideoMetadata

MODEL = "nvidia/Cosmos-Reason2-2B"


def load_video(path: str) -> tuple[np.ndarray, VideoMetadata]:
    """All frames as RGB, with OpenCV (vLLM's own video backend; pyav is not installed)."""
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS)
    frames = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frames.append(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    cap.release()
    arr = np.stack(frames)
    n, h, w = arr.shape[:3]
    meta = VideoMetadata(total_num_frames=n, fps=fps, width=w, height=h, duration=n / fps)
    return arr, meta


def main() -> None:
    path = sys.argv[1]
    fps = float(sys.argv[2]) if len(sys.argv) > 2 else 4.0
    proc = AutoVideoProcessor.from_pretrained(MODEL)
    frames, meta = load_video(path)
    out = proc(videos=[frames], video_metadata=[meta], fps=fps, do_sample_frames=True)
    t, h, w = (int(x) for x in out["video_grid_thw"][0])
    merge = proc.merge_size
    print(
        json.dumps(
            {
                "source_frames": int(frames.shape[0]),
                "source_fps": float(meta.fps),
                "source_hw": [int(frames.shape[1]), int(frames.shape[2])],
                "sample_fps": fps,
                "sampled_frames": t * proc.temporal_patch_size,
                "frame_hw": [h * proc.patch_size, w * proc.patch_size],
                "pixel_budget": {
                    "total_max": proc.size["longest_edge"],
                    "min": proc.size["shortest_edge"],
                },
                "video_tokens": t * h * w // (merge * merge),
            }
        )
    )


if __name__ == "__main__":
    main()
