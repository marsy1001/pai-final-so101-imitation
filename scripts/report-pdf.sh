#!/usr/bin/env bash
# Convert docs/report.md to the submission PDF (A4, Japanese fonts).
#
# Usage:
#   scripts/report-pdf.sh <account> [--input <markdown>] [--output <pdf>]
#
#   <account>   The omnicampus account name the report is submitted under (not
#               the GitHub account; see docs/brief.md). Used only in the file
#               name: tmp/説明資料_PAI最終課題_<account>.pdf (tmp/ is git-ignored)
#   --input     Markdown to convert (default: docs/report.md)
#   --output    Output PDF path (default: the tmp/ path above)
#
# Pipeline: Markdown -> HTML (src/so101_demo/report_pdf.py: Python `markdown`
# with `tables` and `fenced_code`, stylesheet scripts/report-pdf.css) -> PDF through a headless
# Chromium-based browser (`--print-to-pdf`, no header/footer so no URL or date
# lands on the page). Relative image paths resolve against the Markdown
# file's directory, as in the source.
#
# Browser selection: every browser found on this machine is tried in order
# (chromium, chromium-browser, google-chrome, then the Windows Edge executable
# under /mnt/c when running under WSL; paths are converted with wslpath so Edge
# can read and write the WSL files) until one writes the PDF. The browser used
# is reported on stderr. REPORT_PDF_BROWSER=<command or path> tries only that
# one.
#
# Checks after the run: pdfinfo "<pdf>" (Page size: 595 x 842 pts = A4),
# pdftotext "<pdf>" - | head (Japanese headings readable). The browser's own
# output is kept in report-pdf.log next to the PDF (tmp/report-pdf.log by default).
set -euo pipefail

usage() {
  # Header comment (line 2 up to the first non-comment line), '# ' stripped.
  sed -n '2,/^[^#]/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//' >&2
}

root=$(cd "$(dirname "$0")/.." && pwd)
css="$root/scripts/report-pdf.css"
input="$root/docs/report.md"
output=""
account=""

while [ $# -gt 0 ]; do
  case "$1" in
    --input)
      [ $# -ge 2 ] || { usage; exit 2; }
      input=$2
      shift 2
      ;;
    --output)
      [ $# -ge 2 ] || { usage; exit 2; }
      output=$2
      shift 2
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    -*)
      echo "report-pdf: unknown option: $1" >&2
      usage
      exit 2
      ;;
    *)
      if [ -n "$account" ]; then
        echo "report-pdf: unexpected argument: $1" >&2
        usage
        exit 2
      fi
      account=$1
      shift
      ;;
  esac
done

if [ -z "$account" ]; then
  usage
  exit 2
fi
case "$account" in
  */* | *" "*)
    echo "report-pdf: account must not contain '/' or spaces: $account" >&2
    exit 2
    ;;
esac
if [ ! -s "$input" ]; then
  echo "report-pdf: input not found or empty: $input" >&2
  exit 1
fi
input=$(cd "$(dirname "$input")" && pwd)/$(basename "$input")
[ -n "$output" ] || output="$root/tmp/説明資料_PAI最終課題_${account}.pdf"
mkdir -p "$(dirname "$output")"
mkdir -p "$root/tmp"
html=$(mktemp "$root/tmp/report-pdf.XXXXXX.html")
log="$(dirname "$output")/report-pdf.log"
# The browser prints into a fresh temporary PDF; only a non-empty result replaces
# "$output", so an existing PDF is never mistaken for this run's product.
pdf_tmp=$(mktemp "$(dirname "$output")/.report-pdf.XXXXXX.pdf")
trap 'rm -f "$html" "$pdf_tmp"' EXIT

# --- choose the browsers ----------------------------------------------------
# REPORT_PDF_BROWSER names one browser; otherwise every browser found on this
# machine is tried in order until one writes the PDF (a sandbox may let Edge
# run where the snap Chromium cannot create its cgroup scope).
candidates=()
if [ -n "${REPORT_PDF_BROWSER:-}" ]; then
  candidates+=("$REPORT_PDF_BROWSER")
else
  for candidate in chromium chromium-browser google-chrome google-chrome-stable; do
    command -v "$candidate" >/dev/null 2>&1 && candidates+=("$candidate")
  done
  for candidate in "/mnt/c/Program Files/Microsoft/Edge/Application/msedge.exe" \
      "/mnt/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"; do
    [ -x "$candidate" ] && candidates+=("$candidate")
  done
fi
if [ ${#candidates[@]} -eq 0 ]; then
  echo "report-pdf: no Chromium-based browser found (install chromium, or set REPORT_PDF_BROWSER)" >&2
  exit 1
fi

# --- Markdown -> HTML --------------------------------------------------------
(cd "$root" && uv run --quiet python -m so101_demo.report_pdf "$input" "$html" "$css")

# --- HTML -> PDF ----------------------------------------------------------------
# print_pdf <browser>: print $html into $pdf_tmp; true when a non-empty PDF came out.
print_pdf() {
  local browser=$1 html_arg pdf_arg
  if ! command -v "$browser" >/dev/null 2>&1 && [ ! -x "$browser" ]; then
    echo "report-pdf: browser not executable: $browser" >>"$log"
    return 1
  fi
  case "$browser" in
    *.exe)
      # Windows browser: give it Windows paths (\\wsl.localhost\...).
      html_arg=$(wslpath -w "$html")
      pdf_arg=$(wslpath -w "$(dirname "$pdf_tmp")")\\$(basename "$pdf_tmp")
      ;;
    *)
      html_arg="file://$html"
      pdf_arg=$pdf_tmp
      ;;
  esac
  : >"$pdf_tmp"
  echo "report-pdf: trying $browser" >>"$log"
  "$browser" --headless=new --disable-gpu --no-pdf-header-footer \
    --print-to-pdf="$pdf_arg" "$html_arg" >>"$log" 2>&1 && [ -s "$pdf_tmp" ]
}

: >"$log"
used=""
for browser in "${candidates[@]}"; do
  if print_pdf "$browser"; then
    used=$browser
    break
  fi
done
if [ -z "$used" ]; then
  echo "report-pdf: no browser wrote a PDF (tried: ${candidates[*]}); browser output ($log):" >&2
  tail -n 20 "$log" >&2
  exit 1
fi
mv -f "$pdf_tmp" "$output"
echo "report-pdf: printed with $used" >&2
echo "$output"
