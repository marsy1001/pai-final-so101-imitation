#!/usr/bin/env bash
# Register this checkout with agmsg so Claude Code and Codex can message each
# other here. Run once per clone; rerunning is harmless.
#
# What it does (all through agmsg's own scripts, never by editing its files):
#   1. team = directory name (override with AGMSG_TEAM=<name>)
#   2. join agent "claude" (type claude-code) and "codex" (type codex)
#   3. delivery: claude-code -> both, codex -> monitor
#      (writes .claude/settings.local.json and .codex/hooks.json; both git-ignored)
#   4. mark the project trusted in ~/.codex/config.toml so the reviewer can start
#      with an initial prompt and no trust dialog
#
# Requirements: agmsg installed at ~/.agents/skills/agmsg (AGMSG_DIR to override).
set -euo pipefail

AGMSG_DIR="${AGMSG_DIR:-$HOME/.agents/skills/agmsg}"
S="$AGMSG_DIR/scripts"
[ -x "$S/join.sh" ] || { echo "setup: agmsg not found at $AGMSG_DIR" >&2; exit 2; }

PROJECT="$(cd "$(dirname "$0")/.." && pwd -P)"
TEAM="${AGMSG_TEAM:-$(basename "$PROJECT")}"

echo "project: $PROJECT"
echo "team:    $TEAM"

# Refuse to silently share an inbox with another project that already uses
# this team name (same agent names in the same team = shared inbox).
if "$S/team-list.sh" --json --scope all 2>/dev/null | grep -q "\"name\": \"$TEAM\""; then
  other="$("$S/team.sh" "$TEAM" 2>/dev/null | grep -o '/[^ ]*' | grep -v "^$PROJECT$" | sort -u || true)"
  if [ -n "$other" ]; then
    echo "setup: team '$TEAM' already exists for another project:" >&2
    echo "$other" | sed 's/^/  /' >&2
    echo "setup: rerun with AGMSG_TEAM=<unique name> scripts/setup.sh" >&2
    exit 1
  fi
fi

join() {  # join <agent> <type>
  local agent="$1" type="$2"
  if "$S/whoami.sh" "$PROJECT" "$type" 2>/dev/null | grep -q "^agent=$agent "; then
    echo "join:    $agent ($type) already registered"
  else
    "$S/join.sh" "$TEAM" "$agent" "$type" "$PROJECT"
  fi
}
join claude claude-code
join codex codex

echo "delivery: claude-code -> both"
"$S/delivery.sh" set both claude-code "$PROJECT" >/dev/null
echo "delivery: codex -> monitor"
"$S/delivery.sh" set monitor codex "$PROJECT" >/dev/null

# Codex asks "do you trust this directory?" on first launch; with an initial
# prompt (codex-fresh.sh start) that dialog gets in the way. Codex itself
# records the answer in this table, so writing it up front is equivalent.
CFG="$HOME/.codex/config.toml"
if [ -f "$CFG" ] && ! grep -qF "[projects.\"$PROJECT\"]" "$CFG"; then
  printf '\n[projects."%s"]\ntrust_level = "trusted"\n' "$PROJECT" >> "$CFG"
  echo "codex:   marked $PROJECT trusted in $CFG"
fi

cat <<MSG

Done. Next:
  claude                              # in this directory; /agmsg shows the inbox
  scripts/codex-fresh.sh start        # in another terminal, when a task is ready for review
MSG
