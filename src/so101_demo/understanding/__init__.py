"""Understanding layer: Cosmos Reason 2 writes one sentence, GPT-6 Astra turns it into the
contract JSON (design of 2026-09-19: Cosmos never writes JSON, Astra never sees the video).

Nothing here executes anything. Every path ends in `validate_plan` from `so101_demo.plan`,
and every failure (API error, timeout, truncation, refusal, missing tool call, schema
violation) becomes a `status: error` plan, which is never executable.
"""
