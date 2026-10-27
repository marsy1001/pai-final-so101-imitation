#!/usr/bin/env bash
# Start the Codex reviewer in a fresh conversation that receives agmsg messages.
#
# Usage:
#   scripts/codex-fresh.sh start    stop this project's agmsg Codex processes, then
#                                   launch Codex in a NEW conversation whose first
#                                   message is `$agmsg actas codex` (seats it)
#   scripts/codex-fresh.sh stop     stop this project's agmsg Codex processes only
#                                   (nothing runs headlessly while Codex is not in use)
#   scripts/codex-fresh.sh status   show seat / bridge / app-server; exit 0 only when
#                                   app-server and bridge are alive and the bridge
#                                   is bound to the seated thread
#
# Why: agmsg monitor mode pins the codex role to one Codex thread (the "seat")
# and a plain relaunch never moves it: the new thread receives nothing while the
# bridge keeps driving the old thread headlessly (auto-declining every approval
# there). The seat does move when `$agmsg actas codex` runs INSIDE the Codex
# conversation: agmsg's own codex-record-session.sh then sees CODEX_THREAD_ID
# and records the exact thread, and the launcher rebinds the bridge. `/clear`
# does not refresh CODEX_THREAD_ID, so a new conversation needs a new process;
# passing the actas line as Codex's initial prompt makes that one command.
# Checked with agmsg 9cec2d4 and codex 0.154.0 on 2026-09-13.
# Team name = directory name, as
# registered by scripts/setup.sh (override with AGMSG_TEAM / AGMSG_AGENT).
#
# This script never writes or deletes the seat; seating is agmsg's job.
# It only READS these agmsg internals under $AGMSG_DIR/run (agmsg 9cec2d4):
#   role-session.<team>__<agent>                the seat: `session=<thread id>`
#   codex-bridge.<team>.<agent>.thread          thread the live bridge is bound to
#   codex-bridge.<team>.<agent>.pid             bridge pid
#   codex-app-server.<sha1(project)>.pid/.port  app-server pid and ws:// port
# Process command lines matched by `stop` (project path included, so other
# projects' bridges are left alone):
#   codex-bridge-launcher.sh codex <project> ws://...
#   codex-bridge.js --project <project> ...
# If a newer agmsg renames any of these, `status` shows "none"/"not running";
# re-read scripts/drivers/types/codex/codex-bridge-launcher.sh before trusting it.
set -euo pipefail

AGENT="${AGMSG_AGENT:-codex}"
AGMSG_DIR="${AGMSG_DIR:-$HOME/.agents/skills/agmsg}"
RUN_DIR="$AGMSG_DIR/run"
SHIM="$AGMSG_DIR/scripts/drivers/types/codex/codex-shim.sh"
ACTAS_PROMPT='$agmsg actas codex'
BRIDGE_JS="$AGMSG_DIR/scripts/drivers/types/codex/codex-bridge.js"

# Resolve the repository root even when invoked from elsewhere. agmsg hashes the
# logical path it was launched from; `start` cds to this physical path before
# launching, so the two agree unless Codex is later launched by hand through a
# symlinked path (then `status` would not see that app-server).
script_dir="$(cd "$(dirname "$0")" && pwd)"
common_dir="$(git -C "$script_dir" rev-parse --path-format=absolute --git-common-dir)"
PROJECT="$(cd "$(dirname "$common_dir")" && pwd -P)"
PROJECT_HASH="$(printf '%s' "$PROJECT" | sha1sum | awk '{print $1}')"
TEAM="${AGMSG_TEAM:-$(basename "$PROJECT")}"

SEAT_FILE="$RUN_DIR/role-session.${TEAM}__${AGENT}"
BRIDGE_THREAD_FILE="$RUN_DIR/codex-bridge.${TEAM}.${AGENT}.thread"
BRIDGE_PID_FILE="$RUN_DIR/codex-bridge.${TEAM}.${AGENT}.pid"
SERVER_PID_FILE="$RUN_DIR/codex-app-server.${PROJECT_HASH}.pid"
SERVER_PORT_FILE="$RUN_DIR/codex-app-server.${PROJECT_HASH}.port"

usage() { awk 'NR > 3 && /^#$/ { exit } NR >= 2 { sub(/^# ?/, ""); print }' "$0"; }

# pid_alive <pid> <cmdline substring>: true when the pid exists and its command
# line contains the substring (guards against recycled pids from stale pidfiles).
pid_alive() {
  local pid="$1" needle="$2"
  [ -n "$pid" ] && [ -r "/proc/$pid/cmdline" ] || return 1
  tr '\0' ' ' < "/proc/$pid/cmdline" | grep -qF -- "$needle"
}

file_value() { [ -r "$1" ] && head -n1 "$1" | tr -d '\r\n' || true; }
seat_thread() { [ -r "$SEAT_FILE" ] && sed -n 's/^session=//p' "$SEAT_FILE" | head -n1 || true; }

launcher_pids() { pgrep -f "codex-bridge-launcher.sh codex $PROJECT ws://" || true; }
bridge_pids() { pgrep -f "codex-bridge.js --project $PROJECT " || true; }
server_pid() {
  local pid; pid="$(file_value "$SERVER_PID_FILE")"
  pid_alive "$pid" "codex app-server" && printf '%s' "$pid" || true
}

# Codex TUIs attached to this project's app-server (`codex ... --remote ws://127.0.0.1:<port>`).
tui_pids() {
  local port; port="$(file_value "$SERVER_PORT_FILE")"
  [ -n "$port" ] || return 0
  pgrep -f -- "--remote ws://127.0.0.1:$port" || true
}

refuse_if_tui_open() {
  local pids; pids="$(tui_pids)"
  [ -z "$pids" ] && return 0
  echo "codex-fresh: a Codex window is still attached to this project's app-server (pid $(echo "$pids" | tr '\n' ' '))." >&2
  echo "codex-fresh: close it first (or it would lose its backend), then rerun." >&2
  exit 1
}

# Unread agmsg messages addressed to the codex role, without marking them read
# (history.sh shows ● for unread). A relaunched bridge first binds to the OLD
# seat and would consume these headlessly before `actas` moves the seat.
unread_for_codex() {
  "$AGMSG_DIR/scripts/history.sh" "$TEAM" 2>/dev/null | grep -c "^ *● .*→ $AGENT:" || true
}

kill_and_wait() {  # kill_and_wait <label> <pid...>
  local label="$1"; shift
  [ "$#" -gt 0 ] || { echo "$label: not running"; return 0; }
  echo "$label: stopping pid(s) $*"
  kill "$@" 2>/dev/null || true
  local i
  for i in 1 2 3 4 5 6 7 8 9 10; do
    local alive=0 p
    for p in "$@"; do [ -d "/proc/$p" ] && alive=1; done
    [ "$alive" = 0 ] && return 0
    sleep 0.3
  done
  echo "$label: still alive, sending SIGKILL"
  kill -9 "$@" 2>/dev/null || true
}

do_stop() {
  refuse_if_tui_open
  # Launcher first so it does not respawn the bridge; then bridge; then server.
  # shellcheck disable=SC2046
  kill_and_wait "launcher" $(launcher_pids)
  # shellcheck disable=SC2046
  kill_and_wait "bridge" $(bridge_pids)
  local sp; sp="$(server_pid)"
  if [ -n "$sp" ]; then kill_and_wait "app-server" "$sp"; else echo "app-server: not running"; fi
}


loaded_threads() {  # prints loaded thread ids when the app-server answers
  local port; port="$(file_value "$SERVER_PORT_FILE")"
  [ -n "$port" ] && [ -n "$(server_pid)" ] || return 0
  command -v node >/dev/null 2>&1 || return 0
  timeout 10 node "$BRIDGE_JS" --app-server "ws://127.0.0.1:$port" --print-loaded-threads 2>/dev/null || true
}

do_status() {
  local seat bpid bthread sp port ok=1
  seat="$(seat_thread)"
  echo "project:    $PROJECT"
  echo "seat:       ${seat:-none}"
  sp="$(server_pid)"; port="$(file_value "$SERVER_PORT_FILE")"
  if [ -n "$sp" ]; then echo "app-server: alive pid=$sp port=${port:-?}"; else echo "app-server: not running"; fi
  bpid="$(file_value "$BRIDGE_PID_FILE")"
  bthread="$(file_value "$BRIDGE_THREAD_FILE")"
  if pid_alive "$bpid" "codex-bridge.js"; then
    echo "bridge:     alive pid=$bpid thread=${bthread:-?}"
  else
    echo "bridge:     not running${bthread:+ (stale thread record: $bthread)}"
    bpid=""
  fi
  local loaded; loaded="$(loaded_threads)"
  [ -n "$loaded" ] && echo "loaded:     $(printf '%s' "$loaded" | tr '\n' ' ')"
  local tuis; tuis="$(tui_pids)"
  [ -n "$tuis" ] && echo "tui:        pid $(echo "$tuis" | tr '\n' ' ')"
  if [ -n "$seat" ] && [ -n "$bpid" ] && [ "$bthread" = "$seat" ] && [ -z "$sp" ]; then
    echo "result:     app-server not running - the bridge has no backend (orphaned)"
  elif [ -n "$seat" ] && [ -n "$bpid" ] && [ "$bthread" = "$seat" ] \
     && [ -n "$loaded" ] && ! printf '%s\n' "$loaded" | grep -qx "$seat"; then
    # A relaunched bridge re-records the old seat; both agree but no window shows it.
    echo "result:     STALE - seat $seat is not loaded on the live app-server (old conversation)"
  elif [ -n "$seat" ] && [ -n "$bpid" ] && [ "$bthread" = "$seat" ] && [ -n "$sp" ]; then
    echo "result:     OK - agmsg messages reach thread $seat"
    ok=0
  elif [ -z "$seat" ]; then
    echo "result:     no seat - send one message in Codex so the hook seats this conversation"
  elif [ -z "$bpid" ]; then
    echo "result:     bridge not running - messages queue as unread until Codex's next turn"
  else
    echo "result:     MISMATCH - bridge drives $bthread, seat is $seat"
  fi
  return "$ok"
}

do_start() {
  [ -x "$SHIM" ] || { echo "codex-fresh: $SHIM not found (agmsg monitor mode not installed?)" >&2; exit 2; }
  local mode
  mode="$("$AGMSG_DIR/scripts/delivery.sh" status codex "$PROJECT" 2>/dev/null | sed -n 's/^mode: //p' | head -n1)"
  if [ "$mode" != "monitor" ]; then
    echo "codex-fresh: agmsg delivery mode for codex is '${mode:-unknown}', not 'monitor';" >&2
    echo "codex-fresh: the shim would start plain codex with no bridge. Run: delivery.sh set monitor codex $PROJECT" >&2
    exit 2
  fi
  do_stop
  local unread; unread="$(unread_for_codex)"
  if [ "${unread:-0}" -gt 0 ]; then
    echo "codex-fresh: WARNING: $unread unread agmsg message(s) for $AGENT. Until \`actas\` moves the seat," >&2
    echo "codex-fresh: the bridge binds to the OLD conversation and may consume them there. Resend after start." >&2
  fi
  cat <<MSG

Launching Codex in a NEW conversation for $PROJECT.
Its first message is \`$ACTAS_PROMPT\`, which seats this conversation and starts
the bridge; agmsg messages then appear in this window. Nothing to type. Check with:
  scripts/codex-fresh.sh status

MSG
  cd "$PROJECT"
  # The shim routes an interactive launch (prompt included) through
  # codex-monitor.sh, i.e. `codex --remote <app-server> '<prompt>'`. These two
  # variables make it fall through to plain codex; never inherit them.
  unset AGMSG_CODEX_BRIDGE AGMSG_CODEX_SHIM_DISABLE
  exec "$SHIM" "$ACTAS_PROMPT"
}

case "${1:-}" in
  start)  do_start ;;
  stop)   do_stop ;;
  status) do_status ;;
  -h|--help|"") usage; [ -n "${1:-}" ] || exit 2 ;;
  *) echo "codex-fresh: unknown command '$1'" >&2; usage >&2; exit 2 ;;
esac
