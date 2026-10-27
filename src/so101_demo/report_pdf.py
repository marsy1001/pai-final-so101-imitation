"""Markdown -> HTML for scripts/report-pdf.sh (the browser turns the HTML into a PDF).

Usage: python -m so101_demo.report_pdf <markdown> <html> <css>
"""

from __future__ import annotations

import html as html_mod
import os
import re
import sys
from pathlib import Path

import markdown

EXTENSIONS = ["tables", "fenced_code"]
_H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S)
_TAG = re.compile(r"<[^>]+>")


def to_html_body(text: str) -> str:
    """Render Markdown (headings, lists, tables, fenced code, images) to an HTML fragment."""
    return markdown.markdown(text, extensions=EXTENSIONS, output_format="html")


def title_of(body: str, fallback: str) -> str:
    """The first <h1> of the rendered body as plain text, or ``fallback``.

    Reading the rendered HTML (not the Markdown) keeps a ``# comment`` inside a fenced
    code block from being taken as the title.
    """
    m = _H1.search(body)
    if not m:
        return fallback
    return html_mod.unescape(_TAG.sub("", m.group(1))).strip() or fallback


def base_href(src: Path, dst: Path) -> str:
    """Relative URL from the HTML file's directory to the Markdown file's directory.

    Relative links and images in the Markdown then resolve as they do in the source,
    whatever URL scheme the browser opens the HTML with (file:// or \\\\wsl.localhost).
    """
    rel = os.path.relpath(src.resolve().parent, dst.resolve().parent)
    return rel.replace(os.sep, "/") + "/"


def render_page(src: Path, dst: Path, css: Path) -> str:
    text = src.read_text(encoding="utf-8")
    body = to_html_body(text)
    title = title_of(body, src.stem)
    return (
        '<!DOCTYPE html>\n<html lang="ja">\n<head>\n<meta charset="utf-8">\n'
        f"<title>{html_mod.escape(title)}</title>\n"
        f'<base href="{html_mod.escape(base_href(src, dst))}">\n'
        f"<style>\n{css.read_text(encoding='utf-8')}</style>\n</head>\n"
        f"<body>\n{body}\n</body>\n</html>\n"
    )


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    src, dst, css = (Path(a) for a in argv)
    dst.write_text(render_page(src, dst, css), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
