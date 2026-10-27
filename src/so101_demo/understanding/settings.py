"""Operational values of the understanding layer, in one place.

Fixed on 2026-09-28 from the S1 measurements (the 2026-09-28 GPU runs): the provisional
values below held with a wide margin, so they were kept. The measured evidence is in
docs/evidence/s1/diagnosis.md 「運用値」. Over both S1 runs (first
prompt on 004b276, prompt without containers on 8150f07): at most 268 completion tokens,
Cosmos at most 6.6 s, Astra at most 7.7 s, about 115 tokens/s decoding (serving.md).
Every client reads these names; nothing else in the code carries its own number.

Why these values were chosen before the measurement (measured 2026-09-15 on the old
project; the numbers below are the record):

- COSMOS_MAX_TOKENS = 1024. Normal 10 s runs answered in 1.3-7.0 s (stage1) and a median
  of 3.7 s (stage4); the 4 runaway runs of stage4 spent the whole 4096 tokens repeating
  one phrase in about 42 s, i.e. roughly 100 tokens/s. 1024 leaves room for a reasoning
  trace of several hundred tokens plus one sentence, and caps a runaway near 10 s.
- COSMOS_TIMEOUT_S = 30. 1024 tokens at ~100 tokens/s plus the video prefill stays well
  under it; 30 s is half of the provisional median budget of 60 s from video to validated
  JSON (understanding-layer-notes §5). The first request after start-up (17-21 s) is a
  warm-up and is not measured against it.
- ASTRA_TIMEOUT_S = 30. Unmeasured for this account until docs/evidence/s1/astra-roundtrip.md;
  30 s keeps Cosmos + Astra within the 60 s median and far from the 120 s worst case.
- COSMOS_SAMPLE_FPS = 4.0. The handed-over condition (`fps=4.0, do_sample_frames=True`,
  40 frames for 10 s); 4 fps is the official example's value, not a model requirement.
"""

from __future__ import annotations

COSMOS_MAX_TOKENS = 1024
COSMOS_TIMEOUT_S = 30.0
COSMOS_SAMPLE_FPS = 4.0
# Sampling defaults of every run since 2026-09-28; a fixed seed
# does not make the text reproducible, it only keeps the runs comparable.
COSMOS_TEMPERATURE = 0.6
COSMOS_TOP_P = 0.95
COSMOS_TOP_K = 20
COSMOS_SEED = 1234
COSMOS_MODEL = "nvidia/Cosmos-Reason2-2B"
COSMOS_BASE_URL = "http://localhost:8000"

ASTRA_TIMEOUT_S = 30.0

# Deadline of one understanding run (gate.start() -> result settled), after which its result
# is dropped by run ID (understanding.runs, risk R1). Not a separate measured value:
# the two request deadlines back to back, plus a slack for what lies outside the requests
# (thread start / join in post_json, JSON parsing, contract validation, the caller's gap
# between start() and the first request; milliseconds in the tests), so a run whose
# requests both finish in time is not expired by it.
RUN_SLACK_S = 2.0
RUN_TIMEOUT_S = COSMOS_TIMEOUT_S + ASTRA_TIMEOUT_S + RUN_SLACK_S
