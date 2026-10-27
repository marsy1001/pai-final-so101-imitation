"""Understanding layer: Cosmos Reason 2 writes one sentence, GPT-6 Astra turns it into the
contract JSON (ADR 0003, demo-plan 「理解層の設計」).

Nothing here executes anything. Every path ends in `validate_plan` from `so101_demo.plan`,
and every failure (API error, timeout, truncation, refusal, missing tool call, schema
violation) becomes a `status: error` plan, which is never executable.
"""
