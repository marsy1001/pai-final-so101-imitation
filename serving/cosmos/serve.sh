#!/usr/bin/env bash
# Start Cosmos Reason 2 2B on vLLM (ADR 0009). Flags as in
# docs/handover/cosmos-measurements.md §1; procedure in docs/evidence/s1/serving.md.
# Media root: the repository (the S1 videos are under docs/evidence/s1/videos).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
# Triton JIT needs a C compiler; the Zig shim is machine state (cosmos-measurements.md §2)
if [[ -f "$HOME/.local/zigcc/env.sh" ]]; then
  # shellcheck disable=SC1091
  source "$HOME/.local/zigcc/env.sh"
fi
cd "$here"
exec uv run --frozen vllm serve nvidia/Cosmos-Reason2-2B \
  --allowed-local-media-path "$repo" \
  --max-model-len 16384 \
  --media-io-kwargs '{"video": {"num_frames": -1}}' \
  --reasoning-parser qwen3 \
  --limit-mm-per-prompt '{"image": 1, "video": 1}' \
  --gpu-memory-utilization 0.85 \
  --max-num-seqs 1 \
  --port "${PORT:-8000}"
