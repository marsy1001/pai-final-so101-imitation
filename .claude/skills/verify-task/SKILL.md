---
name: verify-task
description: Verify a task end-to-end against its Issue (verification block, acceptance criteria, diff hygiene). The lead runs it before sending READY; the support (mode review) runs the checkout-free steps on the READY commit before sending OK.
---

Run every step from the repository root on the branch under test. Do not skip
a step because the change "looks small". If any step fails, fix and start
again from step 1. Never hand back partially verified work.

1. Read the Issue (`scripts/issue.sh show <n>`). Copy its "検証" code block and run
   every command in it verbatim, in order. Record each command and its exit
   code. For a split Issue (AGENTS.md "Machine split") the Issue has one "検証" block per part;
   run the block of the part under test and only that one.
2. `git diff --check` (whitespace errors) and `git status --porcelain` must be
   empty apart from intended changes; nothing untracked is left behind.
3. If the diff touches code: run the project's lint and tests as listed in
   `AGENTS.md` "Commands", then self-review with `/code-review` and address
   findings.
4. If the diff touches documents: every claim that came from outside the
   repository cites its source (URL or file path). Open each cited URL once
   and confirm it resolves to what the text says. A document that ends in
   the deliverable named by the Issue has a "結論" section first.
5. Confirm every acceptance criterion in the Issue, one by one, and note the
   evidence (command and result, or file and line).
6. Shell scripts under `scripts/`: run at least one real subcommand end to
   end (with `timeout`) and paste its output. `bash -n` is not enough.

Take exit codes from the command itself, not from a pipe's last stage: run
with `set -o pipefail` (or check `${PIPESTATUS[0]}`) whenever the output goes
through `tail`, `grep` or `tee`. A "passed" line in the output is not proof;
the exit code is. (Added 2026-09-19 in the previous project: `pytest | tail` hid a
failure and the checklist reported it as passing.)

For a split Issue (AGENTS.md "Machine split") "a step that could not be run is a failure"
applies to the current part's block and criteria. The other part's criteria
are listed in the checklist as "未達（<part>）" for the record; they do not
fail the current READY.

Report the result as a checklist with the exact commands run and their exit
codes. Post that checklist as a comment on the Issue
(`scripts/issue.sh comment <n> --body-file -`). A step that could not be
run is a failure, not a skip.
