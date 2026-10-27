#!/usr/bin/env bash
# Start Cosmos Reason 2 2B on vLLM (the chosen server for it). Flags and procedure as in
# docs/evidence/s1/serving.md (sections 手順 and 結論, measured 2026-09-28).
# Media root: the repository (the S1 videos are under docs/evidence/s1/videos).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
# Triton JIT needs a C compiler; the Zig shim is machine state (docs/evidence/s1/serving.md, section 環境)
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
