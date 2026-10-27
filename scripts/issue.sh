#!/usr/bin/env bash
# Issue operations for this project, on GitHub Issues or on local files.
#
# Usage:
#   scripts/issue.sh new <title> [--body <text>|--body-file <file>]   create; prints the number
#   scripts/issue.sh show <n>                                         body and comments
#   scripts/issue.sh comment <n> (<text>|--body <text>|--body-file <file>)
#   scripts/issue.sh close <n> [<comment>]
#   scripts/issue.sh reopen <n>
#   scripts/issue.sh list                                             open issues; "[Started]" when work began
#   scripts/issue.sh backend                                          print the backend in use
#
# The backend is the line "- Issue の置き場: github" or "... local" in
# docs/brief.md, decided at kickoff (AGENTS.md); the script stops until it is
# set. `--body-file -` reads stdin.
#   github  `gh issue` on the origin repository, label "task".
#   local   one Markdown file per issue, issues/<n>.md, numbered from 1.
#           issues/ is git-ignored so the support's comments never dirty the
#           lead's working tree. State is the "state:" line; comments are
#           appended as "## comment <date> (<author>)" sections.
# A body that is not given comes from .github/ISSUE_TEMPLATE/task.md without
# its front matter. `new` replaces every `<n>` in the body with the new number
# (both backends), so the `/goal` line is ready to paste.
# Comment author (local): ISSUE_AUTHOR, else "claude" inside Claude Code,
# "codex" inside Codex, else $USER.
set -euo pipefail
cd "$(dirname "$0")/.."

BRIEF=docs/brief.md
TEMPLATE=.github/ISSUE_TEMPLATE/task.md
DIR=issues

usage() { awk 'NR > 3 && /^#$/ { exit } NR >= 2 { sub(/^# ?/, ""); print }' "$0"; }
die() { echo "issue: $*" >&2; exit 2; }

backend() {
  local b
  b="$(sed -n 's/^- Issue の置き場: *\(github\|local\) *$/\1/p' "$BRIEF" 2>/dev/null | head -n1)"
  [ -n "$b" ] || die "backend not set: write '- Issue の置き場: github' or 'local' in $BRIEF (AGENTS.md Kickoff)"
  if [ "$b" = github ]; then
    command -v gh >/dev/null 2>&1 || die "backend is github but gh is not installed (set '- Issue の置き場: local' in $BRIEF to work without it)"
  fi
  printf '%s' "$b"
}

# parse_body <args...>: sets BODY from --body / --body-file / a positional text.
parse_body() {
  BODY=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --body) BODY="${2:?--body needs text}"; shift 2 ;;
      --body-file)
        local f="${2:?--body-file needs a path}"
        if [ "$f" = - ]; then BODY="$(cat)"; else BODY="$(cat "$f")"; fi
        shift 2 ;;
      *) BODY="$1"; shift ;;
    esac
  done
}

template_body() {
  [ -f "$TEMPLATE" ] || die "$TEMPLATE not found"
  awk 'NR == 1 && /^---$/ { fm = 1; next } fm && /^---$/ { fm = 0; blank = 1; next } fm { next }
       blank && !NF { next } { blank = 0; print }' "$TEMPLATE"
}

author() {
  if [ -n "${ISSUE_AUTHOR:-}" ]; then printf '%s' "$ISSUE_AUTHOR"
  elif [ -n "${CLAUDECODE:-}" ]; then printf 'claude'
  elif [ -n "${CODEX_THREAD_ID:-}${CODEX_SANDBOX:-}${CODEX_SANDBOX_NETWORK_DISABLED:-}" ]; then printf 'codex'
  else printf '%s' "${USER:-unknown}"; fi
}

fill_number() { sed "s/<n>/$1/g"; }  # stdin -> stdout

# ---------- github ----------
gh_new() {  # <title> <body>
  local url n
  url="$(gh issue create --label task --title "$1" --body "$2")"
  n="${url##*/}"
  case "$n" in *[!0-9]*|"") die "unexpected output from gh issue create: $url" ;; esac
  if printf '%s' "$2" | grep -qF '<n>'; then
    gh issue edit "$n" --body "$(printf '%s\n' "$2" | fill_number "$n")" >/dev/null
  fi
  echo "$n"
}
# `gh issue view --comments` (gh 2.45) also queries Projects (classic) cards, which GitHub
# removed, and fails; --json asks only for the fields shown here.
gh_show() {
  gh issue view "$1" --json number,title,state,labels,body,comments --jq '
    "#\(.number) \(.title)\nstate: \(.state)  labels: \([.labels[].name] | join(","))\n\n\(.body)\n"
    + ([.comments[] | "\n--- comment \(.createdAt) (\(.author.login))\n\(.body)\n"] | join(""))'
}
gh_comment() { gh issue comment "$1" --body "$2" >/dev/null; }
gh_close() {
  if [ -n "$2" ]; then gh issue close "$1" --comment "$2" >/dev/null; else gh issue close "$1" >/dev/null; fi
}
gh_reopen() { gh issue reopen "$1" >/dev/null; }
gh_list() {
  gh issue list --label task --state open --json number,title --jq '.[] | "  #\(.number) \(.title)"' \
    | while IFS= read -r line; do
        local n started
        n="$(printf '%s' "$line" | sed -E 's/^ *#([0-9]+).*/\1/')"
        started="$(gh issue view "$n" --json comments --jq '[.comments[].body | select(startswith("Started"))] | length' 2>/dev/null)"
        if [ "${started:-0}" -gt 0 ]; then echo "$line  [Started]"; else echo "$line"; fi
      done
}

# ---------- local ----------
local_file() { printf '%s/%s.md' "$DIR" "$1"; }
local_require() { [ -f "$(local_file "$1")" ] || die "issue #$1 not found in $DIR/"; }
local_next() {
  local max=0 f n
  for f in "$DIR"/[0-9]*.md; do
    [ -e "$f" ] || continue
    n="${f##*/}"; n="${n%.md}"
    [ "$n" -gt "$max" ] && max="$n"
  done
  echo $((max + 1))
}
local_new() {  # <title> <body>
  mkdir -p "$DIR"
  local n f
  n="$(local_next)"; f="$(local_file "$n")"
  {
    echo "# $1"
    echo
    echo "state: open"
    echo "created: $(date +%F)"
    echo "labels: task"
    echo
    printf '%s\n' "$2" | fill_number "$n"
  } > "$f"
  echo "issue: created $f" >&2
  echo "$n"
}
local_show() { local_require "$1"; cat "$(local_file "$1")"; }
local_comment() {  # <n> <body>
  local_require "$1"
  {
    echo
    echo "---"
    echo "## comment $(date '+%F %H:%M') ($(author))"
    echo
    printf '%s\n' "$2"
  } >> "$(local_file "$1")"
}
local_set_state() { local_require "$1"; sed -i "s/^state: .*/state: $2/" "$(local_file "$1")"; }
local_close() { local_set_state "$1" closed; local_comment "$1" "${2:-Closed}"; }
local_reopen() { local_set_state "$1" open; local_comment "$1" "Reopened"; }
local_list() {
  local f n title started
  for f in "$DIR"/[0-9]*.md; do
    [ -e "$f" ] || continue
    grep -q '^state: open$' "$f" || continue
    n="${f##*/}"; n="${n%.md}"
    title="$(sed -n '1s/^# //p' "$f")"
    # first non-empty line of any comment starting with "Started"
    if awk '/^## comment /{ c = 1; next } c && NF { print; c = 0 }' "$f" | grep -q '^Started'; then
      started="  [Started]"
    else
      started=""
    fi
    echo "  #$n $title$started"
  done | sort -t'#' -k2 -n
}

# ---------- dispatch ----------
need_n() { case "${1:-}" in ""|*[!0-9]*) die "issue number required" ;; esac; }

cmd="${1:-}"; [ $# -gt 0 ] && shift
case "$cmd" in
  backend) backend; echo ;;
  new)
    title="${1:-}"; [ -n "$title" ] || die "new needs a title"
    shift; parse_body "$@"
    [ -n "$BODY" ] || BODY="$(template_body)"
    case "$(backend)" in github) gh_new "$title" "$BODY" ;; local) local_new "$title" "$BODY" ;; esac ;;
  show)
    need_n "${1:-}"
    case "$(backend)" in github) gh_show "$1" ;; local) local_show "$1" ;; esac ;;
  comment)
    need_n "${1:-}"; n="$1"; shift; parse_body "$@"
    [ -n "$BODY" ] || die "comment needs text"
    case "$(backend)" in github) gh_comment "$n" "$BODY" ;; local) local_comment "$n" "$BODY" ;; esac ;;
  close)
    need_n "${1:-}"
    case "$(backend)" in github) gh_close "$1" "${2:-}" ;; local) local_close "$1" "${2:-}" ;; esac ;;
  reopen)
    need_n "${1:-}"
    case "$(backend)" in github) gh_reopen "$1" ;; local) local_reopen "$1" ;; esac ;;
  list)
    case "$(backend)" in github) gh_list ;; local) local_list ;; esac ;;
  -h|--help|"") usage; [ -n "$cmd" ] || exit 2 ;;
  *) echo "issue: unknown command '$cmd'" >&2; usage >&2; exit 2 ;;
esac
