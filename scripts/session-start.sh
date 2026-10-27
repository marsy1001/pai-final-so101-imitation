#!/usr/bin/env bash
# SessionStart hook: print the repository's work-in-progress so a fresh
# session knows where things stand (AGENTS.md "On session start").
# Output goes to the agent's context. Keep it short; never print secrets.
set -uo pipefail
cd "$(dirname "$0")/.."
echo "## $(basename "$PWD") session start"
echo "branch: $(git branch --show-current 2>/dev/null) @ $(git log --oneline -1 2>/dev/null)"
if grep -q "（なぜやるか" docs/brief.md 2>/dev/null; then
  echo "brief not filled: docs/brief.md still has placeholders. Do the Kickoff in AGENTS.md before any task."
fi
if backend="$(scripts/issue.sh backend 2>/dev/null)"; then
  echo "open task issues ($backend; Started = approved and work began):"
  scripts/issue.sh list 2>&1
else
  echo "issue backend not set in docs/brief.md (Kickoff step); skipping issue list"
fi
# The conditionals above are allowed to be false; that must not become the
# hook's exit status (Claude Code reports a non-zero exit as a hook error).
exit 0
