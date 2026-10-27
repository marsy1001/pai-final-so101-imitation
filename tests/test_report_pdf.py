"""Markdown -> HTML step of scripts/report-pdf.sh."""

from pathlib import Path

from so101_demo import report_pdf

CSS = Path(__file__).resolve().parents[1] / "scripts" / "report-pdf.css"


def test_title_comes_from_the_first_heading_not_a_fenced_comment():
    body = report_pdf.to_html_body("```bash\n# not a title\n```\n\n# 見出し `code`\n")
    assert report_pdf.title_of(body, "fallback") == "見出し code"
    assert report_pdf.title_of("<p>no heading</p>", "fallback") == "fallback"


def test_tables_code_and_images_render(tmp_path):
    md = tmp_path / "docs" / "r.md"
    md.parent.mkdir()
    md.write_text(
        "# T\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n```\nx = 1\n```\n\n![f](evidence/f.png)\n",
        encoding="utf-8",
    )
    page = report_pdf.render_page(md, tmp_path / "tmp" / "r.html", CSS)
    assert "<table>" in page and "<td>1</td>" in page
    assert "<pre><code>x = 1" in page
    assert '<img alt="f" src="evidence/f.png"' in page
    assert '<base href="../docs/">' in page
    assert "<title>T</title>" in page
    assert "@page" in page


def test_main_writes_the_html(tmp_path):
    md = tmp_path / "in.md"
    md.write_text("# A\n\ntext\n", encoding="utf-8")
    out = tmp_path / "out.html"
    assert report_pdf.main([str(md), str(out), str(CSS)]) == 0
    assert "<h1>A</h1>" in out.read_text(encoding="utf-8")
    assert report_pdf.main([]) == 2


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "report-pdf.sh"


def _run_script(tmp_path, browser_body: str, existing: bytes | None):
    """Run report-pdf.sh with a stub browser; return (exit code, output PDF path, stderr)."""
    import subprocess

    stub = tmp_path / "browser"
    stub.write_text("#!/usr/bin/env bash\n" + browser_body, encoding="utf-8")
    stub.chmod(0o755)
    md = tmp_path / "in.md"
    md.write_text("# 新しい見出し\n", encoding="utf-8")
    out = tmp_path / "out.pdf"
    if existing is not None:
        out.write_bytes(existing)
    proc = subprocess.run(
        ["bash", str(SCRIPT), "ACCOUNT", "--input", str(md), "--output", str(out)],
        env={**__import__("os").environ, "REPORT_PDF_BROWSER": str(stub)},
        capture_output=True,
        text=True,
        timeout=120,
    )
    return proc.returncode, out, proc.stderr


def test_browser_that_writes_nothing_is_a_failure_even_with_an_old_pdf(tmp_path):
    old = b"%PDF-old"
    code, out, err = _run_script(tmp_path, "exit 0\n", existing=old)
    assert code == 1, err
    assert out.read_bytes() == old  # the stale file is left alone, never reported as new
    assert "no browser wrote a PDF" in err
    out.unlink()
    code, out, err = _run_script(tmp_path, "exit 0\n", existing=None)
    assert code == 1 and not out.exists()


def test_browser_output_replaces_the_old_pdf(tmp_path):
    # The stub writes the file named by --print-to-pdf=<path>.
    body = (
        'for a in "$@"; do case "$a" in '
        '--print-to-pdf=*) printf "%%PDF-new" > "${a#*=}";; esac; done\n'
    )
    code, out, err = _run_script(tmp_path, body, existing=b"%PDF-old")
    assert code == 0, err
    assert out.read_bytes() == b"%PDF-new"
    assert not list(tmp_path.glob(".report-pdf.*"))  # no temporary PDF left behind


def test_the_next_detected_browser_is_tried_when_the_first_fails(tmp_path):
    import os
    import subprocess

    stubs = tmp_path / "bin"
    stubs.mkdir()
    (stubs / "chromium").write_text("#!/usr/bin/env bash\necho 'no cgroup scope' >&2\nexit 1\n")
    (stubs / "chromium-browser").write_text(
        "#!/usr/bin/env bash\n"
        'for a in "$@"; do case "$a" in '
        '--print-to-pdf=*) printf "%%PDF-2" > "${a#*=}";; esac; done\n'
    )
    for stub in stubs.iterdir():
        stub.chmod(0o755)
    md = tmp_path / "in.md"
    md.write_text("# A\n", encoding="utf-8")
    out = tmp_path / "out.pdf"
    env = {k: v for k, v in os.environ.items() if k != "REPORT_PDF_BROWSER"}
    env["PATH"] = f"{stubs}{os.pathsep}{env['PATH']}"
    proc = subprocess.run(
        ["bash", str(SCRIPT), "ACCOUNT", "--input", str(md), "--output", str(out)],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert out.read_bytes() == b"%PDF-2"
    assert "printed with chromium-browser" in proc.stderr
