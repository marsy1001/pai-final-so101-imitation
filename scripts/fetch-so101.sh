#!/usr/bin/env bash
# Fetch TheRobotStudio/SO-ARM100 `Simulation/SO101/` at the pinned revision into
# assets/so101/ and verify it against the committed MANIFEST.sha256.
#
#   scripts/fetch-so101.sh                  # download if missing, then verify (exit 1 on any difference)
#   scripts/fetch-so101.sh --force          # re-download, then verify
#   scripts/fetch-so101.sh --write-manifest # fresh download, then (re)write the manifest
#
# The manifest lists every file under assets/so101/ except itself; a normal run
# never rewrites it, and --write-manifest always re-downloads so a locally edited
# tree can never be recorded as the pinned revision.
set -euo pipefail

REPO="TheRobotStudio/SO-ARM100"
REV="eecbe3e0a9ebb23e25ad7b2759b03884c6660903"
SUBDIR="Simulation/SO101"

ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
DEST="$ROOT/assets/so101"
MANIFEST="$DEST/MANIFEST.sha256"

force=0; write=0
for a in "$@"; do
  case "$a" in
    --force) force=1 ;;
    --write-manifest) write=1; force=1 ;;
    *) echo "usage: $0 [--force] [--write-manifest]" >&2; exit 2 ;;
  esac
done

have_files() { [ -d "$DEST" ] && [ -n "$(find "$DEST" -type f ! -name MANIFEST.sha256 -print -quit)" ]; }

if [ "$force" = 1 ] || ! have_files; then
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
  url="https://github.com/$REPO/archive/$REV.tar.gz"
  echo "fetch: $url"
  curl -fsSL "$url" -o "$tmp/src.tar.gz"
  tar -xzf "$tmp/src.tar.gz" -C "$tmp"
  src="$tmp/SO-ARM100-$REV/$SUBDIR"
  [ -d "$src" ] || { echo "fetch: $SUBDIR not found in archive" >&2; exit 1; }
  mkdir -p "$DEST"
  find "$DEST" -mindepth 1 ! -name MANIFEST.sha256 -exec rm -rf {} + 2>/dev/null || true
  cp -R "$src"/. "$DEST"/
  echo "fetch: extracted $SUBDIR@$REV to $DEST"
fi

cd "$DEST"
if [ "$write" = 1 ]; then
  find . -type f ! -name MANIFEST.sha256 -printf '%P\0' | LC_ALL=C sort -z | xargs -0 sha256sum > MANIFEST.sha256
  echo "manifest: wrote $(wc -l < MANIFEST.sha256) entries to $MANIFEST"
fi

[ -s MANIFEST.sha256 ] || { echo "verify: $MANIFEST missing; run with --write-manifest once" >&2; exit 1; }

# same file set (catches extra or missing files), then same content
expected="$(cut -c67- MANIFEST.sha256 | LC_ALL=C sort)"
actual="$(find . -type f ! -name MANIFEST.sha256 -printf '%P\n' | LC_ALL=C sort)"
if [ "$expected" != "$actual" ]; then
  echo "verify: file list differs from manifest" >&2
  diff <(echo "$expected") <(echo "$actual") >&2 || true
  exit 1
fi
if ! sha256sum -c --quiet MANIFEST.sha256; then
  echo "verify: checksum mismatch (see above)" >&2
  exit 1
fi
echo "verify: OK ($(wc -l < MANIFEST.sha256) files match $REV)"
