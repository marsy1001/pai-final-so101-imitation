# pai-final-so101-imitation

Submission for the Matsuo Lab "Physical AI 応用編 1" final assignment: a
short video of a person moving a cube is read by an understanding layer
(Cosmos Reason 2 sentence, GPT-6 Astra contract JSON), approved on screen,
and reproduced by an SO-101 in MuJoCo. Purpose, scope, deliverables and the
assignment's requirements live in `docs/brief.md`; read it before any task.

Two agents work here, talking through agmsg: Claude Code is the **lead**
(does the work, owns the branch, merges) and Codex is the **support** (reads,
checks, answers; never writes to git). The support's job is named per Issue
(see "Support"): `review` for code, `verify` for documents. Work is tracked
as GitHub Issues through `scripts/issue.sh`.

## Stack
- Python 3.12, uv (`pyproject.toml`, `uv.lock`). Run anything with `uv run <cmd>`
- MuJoCo 3.13.0. SO-101 model: TheRobotStudio/SO-ARM100 `Simulation/SO101/`
  at eecbe3e, fetched by `scripts/fetch-so101.sh` (only the manifest is tracked)
- IK: LeRobot `RobotKinematics` (placo) at 5aa7455, `so101_new_calib.urdf`
  (vendored under `src/so101_demo/vendor/`)
- Understanding layer: Cosmos Reason 2 2B on vLLM 0.12.0 (local, separate uv
  project `serving/cosmos/`), GPT-6 Astra through the Azure OpenAI Responses
  API (keys in `.env`, see `.env.example`)
- Approval screen and big screen: FastAPI + static HTML/JS (`src/so101_demo/ui/`)
- Lint: ruff (check + format). Tests: pytest. Tests that need the GPU, vLLM or
  a display are marked `gpu` and are skipped by default

## Commands
```bash
scripts/setup.sh                        # register both agents in agmsg for this checkout (run once per clone)
scripts/fetch-so101.sh                  # fetch the pinned SO-101 model into assets/so101/ (run once per clone)
scripts/codex-fresh.sh start|stop|status  # support Codex: fresh conversation per work session (see Support)
scripts/issue.sh list                   # open tasks
uv run ruff check . && uv run ruff format --check .   # lint, part of verify-task
uv run pytest                           # tests (gpu-marked skipped), part of verify-task
git diff --check                        # part of verify-task
```

## Conventions
- Code, comments, identifiers, commit messages: English. Chat with the user: Japanese.
- Issues (title, body, comments) and documents under `docs/`: Japanese.
  Code, identifiers and commands inside them stay English.
- Documents: conclusion first, then evidence, then sources (URL or path).
  Absolute dates only (YYYY-MM-DD).
- Secrets never enter the repository. `.env` is git-ignored and denied to
  agents by `.claude/settings.json`; scripts that need a key read `.env`
  themselves (`.env.example` lists the keys).
- **The repository becomes public at submission.** No personal names,
  affiliations, e-mail addresses, home paths or machine names in tracked
  files or commit messages (the assignment forbids personal information in
  the deliverables). Commits are authored with the GitHub noreply address
  set in this checkout's `git config`. Videos show hands and cubes only.
- Cross-project lessons about the agent workflow go to
  `~/work/notes/topics/agentic-development.md`, not here.

## Development process

### Issue backend
`docs/brief.md` names where Issues live (`github` here); `scripts/issue.sh`
reads that line and offers `new`, `show`, `comment`, `close`, `reopen`,
`list`. Issues carry the label `task`; the session-start hook lists only
those. Both agents share one GitHub account, so GitHub approvals are not
used. No milestones and no GitHub Project: the brief's deadline is the only
schedule.

### Branches
- `main`: reviewed work only. Every task branch reaches `main` through the
  review below. Never commit task work directly to `main`.
- `task/<n>-<slug>` one branch per Issue, branched from `main`, squashed back
  into `main` by the lead after the support's `OK`. An Issue with a machine
  split has two: `task/<n>-<slug>` for the laptop part and
  `task/<n>-<slug>-gpu` for the GPU-machine part, each reviewed and squashed
  on its own; the Issue closes with the second.
- Exception: changes only to process files (`AGENTS.md`, `README.md`,
  `scripts/`, `.github/`, `.claude/`, `docs/templates/`, `docs/brief.md`)
  may be committed on `main` directly after `git diff --check`; no Issue and
  no review needed.
- No worktrees: one task branch at a time, the lead works on it in the
  repository root. The support reads by commit hash and never checks
  anything out. After a laptop part is merged, the lead may start the next
  Issue's laptop part while that Issue's GPU part waits for the machine.

### Machine split
Two machines: the laptop (WSL2, 4 CPUs / 8 GB, no NVIDIA GPU; always
available) and the GPU machine (RTX 4090; limited hours). Three kinds of
work go to the GPU machine: anything that needs rendering (videos, fps,
screen captures), GPU inference (Cosmos / vLLM) and end-to-end runs in the
submission configuration. Headless physics, unit tests, documents and the
video script are laptop work. An Issue that has both parts writes them
apart in its "端末分担" section (template): laptop acceptance criteria and
verification commands, GPU-machine acceptance criteria and commands, and
the hand-over evidence (commit, config id, run commands, all trial logs,
known failures). Laptop runs of a GPU-machine procedure are labelled 参考
in the evidence. Each part has its own `/goal` and its own "Started"
comment.

### Task cycle (one Issue = one loop; two READY/OK rounds when split)
1. Discuss the next piece of work with the user in chat.
2. The agent files an Issue with `scripts/issue.sh new <title> --body-file
   <file>`, following `.github/ISSUE_TEMPLATE/task.md`: goal (naming the
   deliverable file), acceptance criteria (machine-checkable; never "the
   user confirms"), verification commands, the support mode (`review` or
   `verify`), limits.
3. The Issue body ends with a ready-to-paste `/goal` line. `issue.sh new`
   replaces `<n>` with the real number; print the line in chat without being
   asked. Work starts only when the user approves:
   - pasting the `/goal` line into the session, or
   - a chat message naming the Issues to run in order ("#3 #4 を順にやって").
     Then the agent runs them back to back, one Issue at a time, applying
     each Issue's limits itself, and stops at the first ESCALATE.
   On start, the agent comments "Started" on the Issue
   (`scripts/issue.sh comment <n> Started`).
4. Work on the task branch. Iterate work -> `verify-task` skill -> fix until
   every acceptance criterion of the current part passes (the whole Issue
   when it is not split; the laptop or GPU-machine part when it is). When a
   criterion turns out to be wrong or impossible, amend it on the Issue with
   the reason and continue; stop for the user only when the amendment
   changes the Issue's scope. Limits per Issue: 5 verification rounds, 3
   review round-trips, 20 turns and 60 minutes per `/goal`, 2 days. On
   reaching one, ESCALATE.
5. Push the branch (`git push -u origin <branch>`), run
   `scripts/codex-fresh.sh status`, and send `READY <branch> <commit>` on
   agmsg only when it reports OK. For a split Issue the laptop READY covers
   only the laptop criteria; say so in the Issue comment that carries the
   verify-task checklist. Otherwise do not send: tell the user to run
   `scripts/codex-fresh.sh start` and wait (a READY sent to a stale bridge
   is consumed by an old conversation).
6. The support checks the commit (see "Support"). On `CHANGES #<n>`, fix on
   the same branch and send `READY` again; no new `/goal` is needed. On
   `OK <commit>`, the lead squashes the branch into `main`
   (`git checkout main && git merge --squash <branch>`), commits with the
   Issue number and the verification evidence, pushes, deletes the branch
   (local and remote) and closes the Issue with the commit hash
   (`scripts/issue.sh close <n> "merged: <hash>"`). Never write `Closes #<n>`
   in the merge commit: GitHub closes the Issue on push and `issue.sh close`
   then skips, so the merge hash is never recorded. For the laptop part of a
   split Issue: merge, delete the branch, comment "laptop 分マージ: <hash>"
   and leave the Issue open; the GPU part later starts from `main` on
   `task/<n>-<slug>-gpu` and its `OK` closes the Issue. Review round-trips
   (3) count per READY; verification rounds (5) and the 2 days count per
   Issue.
7. The user reads the deliverable on `main` when they want to. A problem
   found later becomes a new Issue.

### Support (before merge)
- Roles are fixed: Claude Code leads, Codex supports. The support never
  writes to the repository: no commits, no checkout, no file edits. Its
  output is Issue comments (`scripts/issue.sh comment`) and one-line agmsg
  signals. (Codex's sandbox cannot write `.git` in the repository root
  anyway.)
- The Issue names the support mode in its "担当" section. Both modes share
  the same signals and the same Issue comment format.
  - `review` (code, scripts, config): read the diff by hash
    (`git diff main..<commit>`), run the Issue's verification commands and
    the `verify-task` steps that need no checkout, then look for
    correctness bugs, unmet acceptance criteria and missing docs.
    Findings as `file:line`.
  - `verify` (documents: report, video script, README): read the
    deliverable by hash (`git show <commit>:docs/<file>`). Check that every
    external claim has a source and that the source says what the text says
    (open it). Check the assignment's requirements in `docs/brief.md`
    (length, naming, no personal information) and look for the strongest
    counter-argument or alternative the document does not address.
    Findings as document section + what is missing or wrong. A finding is
    not "I would have written it differently"; it is a claim without
    support, a source that says otherwise, or a requirement left open.
- The support Codex is started with `scripts/codex-fresh.sh start` once per
  work session (not per Issue). It launches Codex with `$agmsg actas codex`
  as the first message, which seats that conversation: agmsg monitor mode
  pins the codex role to one thread, and a plain relaunch or `/clear` keeps
  delivering to the old one. Never use `/clear` in the support. Close the
  Codex window before `stop` or `start`. Run `scripts/codex-fresh.sh stop`
  when Codex is not in use; otherwise the bridge keeps acting on messages
  headlessly with approvals auto-declined.
- On `READY <branch> <commit>` the support does the mode's checks, posts the
  evidence (commands run, sources opened) as a comment on the Issue, then:
  - OK: comment "OK" on the Issue and send `OK <commit>`.
  - NG: post findings on the Issue as comments and send `CHANGES #<n>`. At
    most 3 round-trips; if the third `CHANGES` does not resolve it, the
    lead ESCALATEs.
- The lead may also ask the support a bounded question during work:
  `ASK <issue> <question>` on agmsg. The support answers on the Issue as a
  comment (with sources) and replies `ANSWERED <issue>` on agmsg. The lead
  continues without waiting for the user. Use this for a second opinion or
  a side lookup, not to hand over the task.
- agmsg carries only one-line signals: `READY <branch> <commit>`,
  `OK <commit>`, `CHANGES #<n>`, `ASK <issue> <question>`,
  `ANSWERED <issue>`, `ESCALATE <issue> <reason>`. Everything else goes on
  the Issue.

### Handling agmsg messages
- A message that contains a clear request is acted on immediately, without
  asking the user for confirmation; the action follows the content of the
  message (`READY` -> check it, `CHANGES #n` -> fix on the branch,
  `OK` -> merge and close, `ASK` -> answer on the Issue, a free-form
  question -> answer on agmsg).
- A `READY` is carried through to the end: evidence and findings on the
  Issue, then `OK` or `CHANGES`.
- The only exceptions that require the user's confirmation are irreversible
  operations (deleting branches or data other than the task's own branch,
  force-pushing, rewriting `main`, changing the repository's visibility)
  and operations that need permissions the agent does not have. Starting an
  Issue is still gated by the user's approval (see "Task cycle").
- A message that is unclear or unactionable gets a one-line reply asking for
  the missing detail; it is not silently ignored.
- An `ESCALATE` whose cause has a documented remedy (an ADR, this file, a
  script's usage text) is answered on agmsg with that remedy at once; the
  receiving agent does not stop for the user. Only an `ESCALATE` with no
  documented remedy waits for the user.

### Limits and escalation
- At most 2 agents run concurrently (one Claude Code, one Codex). Subagents
  inside them are fine.
- A task should finish within 2 days; if not, split it.
- ESCALATE means: stop, post a comment on the Issue explaining what was
  tried and what is blocked, and wait for the user.
- An Issue that edits `AGENTS.md` runs outside Claude Code's auto mode: the
  auto mode classifier blocks commits that change agent instructions.

### Learning loop
- When an ESCALATE happens, or the same kind of review finding appears twice,
  add one line to this file or to the `verify-task` skill so it does not
  recur. Record the observation in
  `~/work/notes/topics/agentic-development.md`.

## Documents
- `docs/brief.md` purpose, assignment requirements, scope, deliverables,
  deadline. Amended with dates.
- `docs/report.md` the report (source of the submitted PDF). Template:
  `docs/templates/report.md`. Conclusion first.
- `docs/evidence/` small reviewed artifacts (evaluation records, short sim
  clips up to about 10 MB each). Videos that show people, the submitted MP4
  and the PDF with the account name stay under git-ignored `tmp/`.
- `docs/adr/NNNN-<slug>.md` decisions, one per file, never edited after
  acceptance; supersede with a new ADR. Body in Japanese, filename and title
  in English. Template: `docs/templates/adr.md`. Required for: choice of an
  external service or tool, a change to the process in this file, a change
  of deliverable. Only the user sets an ADR's status to Accepted.

## Working with the user
- When several decisions are pending, raise them one at a time: a short list
  of upcoming topics is fine, but give details and ask for a decision on
  only one topic per reply. Offer a recommendation with reasons and at most
  3 options.
- Never edit files outside this repository (e.g. `~/.claude/CLAUDE.md`)
  unless explicitly asked. Process and agent rules live in this file.
- Do not commit or push unless asked. The task cycle above counts as asked:
  committing on the task branch, pushing it, and merging into `main` after
  `OK`.
- On session start, a hook (`scripts/session-start.sh`, wired in
  `.claude/settings.json`) prints open task Issues via `scripts/issue.sh
  list` (marking those already Started) and the current branch. Read it;
  resume a Started Issue only when the user approves it again.
