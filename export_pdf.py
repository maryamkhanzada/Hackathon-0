"""
export_pdf.py — Markdown to PDF Exporter for Personal AI Employee (Gold Tier)

Converts README_Gold.md (or any Markdown file) to PDF using the best available
backend, with automatic fallback:

  Backend priority:
    1. weasyprint   — high-fidelity HTML→PDF, preserves code blocks + tables
    2. pdfkit       — wkhtmltopdf wrapper (requires wkhtmltopdf binary)
    3. reportlab    — pure-Python PDF writer (no system deps)
    4. text fallback— writes .txt copy and prints manual export instructions

Usage:
    python export_pdf.py                                      # default: README_Gold.md
    python export_pdf.py --input README_Gold.md               # explicit input
    python export_pdf.py --input README_Gold.md --output Docs/README_Gold.pdf
    python export_pdf.py --check                              # check available backends
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import textwrap
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_VAULT_ROOT = Path(__file__).resolve().parent
_DOCS_DIR   = _VAULT_ROOT / "Docs"
_DOCS_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Markdown → HTML conversion (shared by weasyprint and pdfkit backends)
# ---------------------------------------------------------------------------

_CSS = """
body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    font-size: 13px;
    line-height: 1.6;
    max-width: 960px;
    margin: 0 auto;
    padding: 20px 40px 40px;
    color: #24292e;
}
h1 { font-size: 2em;   border-bottom: 2px solid #e1e4e8; padding-bottom: 0.3em; }
h2 { font-size: 1.5em; border-bottom: 1px solid #e1e4e8; padding-bottom: 0.3em; margin-top: 2em; }
h3 { font-size: 1.2em; margin-top: 1.5em; }
code, pre { font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace; }
pre {
    background: #f6f8fa;
    border: 1px solid #e1e4e8;
    border-radius: 6px;
    padding: 12px 16px;
    overflow-x: auto;
    font-size: 12px;
    line-height: 1.45;
    white-space: pre;
}
code { background: #f3f4f6; padding: 2px 5px; border-radius: 3px; font-size: 92%; }
pre code { background: none; padding: 0; border-radius: 0; font-size: inherit; }
table { border-collapse: collapse; width: 100%; margin: 1em 0; font-size: 12px; }
th, td { border: 1px solid #dfe2e5; padding: 6px 13px; text-align: left; }
th { background: #f6f8fa; font-weight: 600; }
tr:nth-child(even) { background: #fafbfc; }
blockquote {
    margin: 0; padding: 0 1em;
    color: #6a737d;
    border-left: 4px solid #dfe2e5;
}
hr { border: none; border-top: 2px solid #e1e4e8; margin: 2em 0; }
.page-break { page-break-before: always; }
@media print {
    body { max-width: none; padding: 10px; }
    pre { white-space: pre-wrap; word-wrap: break-word; }
}
"""

def _md_to_html(md_text: str, title: str = "README_Gold") -> str:
    """Convert Markdown text to a complete HTML document."""
    try:
        import markdown as md_lib
        body = md_lib.markdown(
            md_text,
            extensions=["tables", "fenced_code", "codehilite",
                        "toc", "nl2br", "attr_list"],
        )
    except ImportError:
        # Minimal fallback converter (handles headings, code blocks, bold)
        body = _minimal_md_to_html(md_text)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>{_CSS}</style>
</head>
<body>
{body}
<footer style="margin-top:3em;color:#6a737d;font-size:11px;border-top:1px solid #e1e4e8;padding-top:8px;">
Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} — Personal AI Employee Gold Tier
</footer>
</body>
</html>"""


def _minimal_md_to_html(text: str) -> str:
    """Bare-minimum Markdown→HTML without the markdown package."""
    import html as html_lib
    lines = text.splitlines()
    out   = []
    in_code = False
    in_table = False

    for line in lines:
        # Code blocks
        if line.startswith("```"):
            if in_code:
                out.append("</code></pre>")
                in_code = False
            else:
                lang = line[3:].strip()
                out.append(f'<pre><code class="language-{lang}">')
                in_code = True
            continue
        if in_code:
            out.append(html_lib.escape(line))
            continue

        # Horizontal rule
        if re.match(r"^-{3,}$", line) or re.match(r"^={3,}$", line):
            out.append("<hr>")
            continue

        # Headings
        m = re.match(r"^(#{1,6})\s+(.*)", line)
        if m:
            level = len(m.group(1))
            content = _inline_md(m.group(2))
            out.append(f"<h{level}>{content}</h{level}>")
            continue

        # Table rows
        if "|" in line:
            cells = [c.strip() for c in line.strip("|").split("|")]
            if re.match(r"^[|\-: ]+$", line):  # separator row
                continue
            tag = "th" if not in_table else "td"
            in_table = True
            row = "".join(f"<{tag}>{_inline_md(c)}</{tag}>" for c in cells)
            out.append(f"<tr>{row}</tr>")
            continue
        elif in_table:
            out.append("</table>")
            in_table = False

        # Bullet lists
        if re.match(r"^[-*+]\s+", line):
            out.append(f"<li>{_inline_md(line[2:].strip())}</li>")
            continue

        # Numbered lists
        if re.match(r"^\d+\.\s+", line):
            out.append(f"<li>{_inline_md(re.sub(r'^\d+\.\s+','',line))}</li>")
            continue

        # Blockquote
        if line.startswith(">"):
            out.append(f"<blockquote>{_inline_md(line[1:].strip())}</blockquote>")
            continue

        # Normal paragraph
        stripped = line.strip()
        if stripped:
            out.append(f"<p>{_inline_md(stripped)}</p>")
        else:
            out.append("")

    if in_table:
        out.append("</table>")

    # Wrap consecutive <li> in <ul>
    result = "\n".join(out)
    result = re.sub(r"(<li>.*?</li>\n?)+",
                    lambda m: f"<ul>{m.group(0)}</ul>\n", result, flags=re.DOTALL)
    # Wrap table rows
    result = re.sub(r"(<tr>.*?</tr>\n?)+",
                    lambda m: f"<table>{m.group(0)}</table>\n", result, flags=re.DOTALL)
    return result


def _inline_md(text: str) -> str:
    """Convert inline Markdown (bold, italic, code, links) to HTML."""
    import html as html_lib
    text = html_lib.escape(text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    return text


# ---------------------------------------------------------------------------
# Backend implementations
# ---------------------------------------------------------------------------


def _backend_weasyprint(html: str, out_path: Path) -> bool:
    """Render HTML to PDF using WeasyPrint (requires Pango/GTK on Windows)."""
    try:
        from weasyprint import HTML
        HTML(string=html).write_pdf(str(out_path))
        return True
    except (ImportError, OSError):
        # OSError on Windows when GTK/Pango system libs are missing
        return False
    except Exception as exc:
        print(f"  [weasyprint] Error: {exc}")
        return False


def _backend_pdfkit(html: str, out_path: Path) -> bool:
    """Render HTML to PDF using pdfkit (requires wkhtmltopdf binary)."""
    try:
        import pdfkit
        pdfkit.from_string(html, str(out_path), options={"quiet": ""})
        return True
    except ImportError:
        return False
    except Exception as exc:
        print(f"  [pdfkit] Error: {exc}")
        return False


def _backend_reportlab(md_text: str, out_path: Path) -> bool:
    """Produce a simple plain-text PDF via ReportLab (no HTML rendering)."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import (Paragraph, SimpleDocTemplate,
                                         Spacer, Preformatted)
        from reportlab.lib.units import cm

        doc    = SimpleDocTemplate(str(out_path), pagesize=A4,
                                   leftMargin=2*cm, rightMargin=2*cm,
                                   topMargin=2*cm, bottomMargin=2*cm)
        styles = getSampleStyleSheet()
        story  = []
        in_code = False
        code_lines: list[str] = []

        for line in md_text.splitlines():
            if line.startswith("```"):
                if in_code:
                    story.append(Preformatted("\n".join(code_lines),
                                              styles["Code"]))
                    story.append(Spacer(1, 6))
                    code_lines = []
                    in_code = False
                else:
                    in_code = True
                continue
            if in_code:
                code_lines.append(line)
                continue

            m = re.match(r"^(#{1,6})\s+(.*)", line)
            if m:
                level   = len(m.group(1))
                content = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", m.group(2))
                sname   = {1: "Heading1", 2: "Heading2", 3: "Heading3"}.get(level, "Heading4")
                story.append(Paragraph(content, styles[sname.capitalize()]))
                story.append(Spacer(1, 4))
            elif line.strip():
                safe = re.sub(r"[<>&]", lambda c: {"<":"&lt;",">":"&gt;","&":"&amp;"}[c.group()], line)
                safe = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", safe)
                story.append(Paragraph(safe, styles["Normal"]))
            else:
                story.append(Spacer(1, 8))

        doc.build(story)
        return True
    except ImportError:
        return False
    except Exception as exc:
        print(f"  [reportlab] Error: {exc}")
        return False


def _backend_text_copy(md_text: str, out_path: Path) -> bool:
    """Write a .txt copy and print manual PDF instructions."""
    txt_path = out_path.with_suffix(".txt")
    txt_path.write_text(md_text, encoding="utf-8")
    msg = (
        "\n  [fallback] Plain-text copy written: " + str(txt_path) + "\n\n"
        "  No PDF backend available. Manual export options:\n"
        "  -------------------------------------------------\n"
        "  A) Obsidian:  Open README_Gold.md -> Ctrl+P -> 'Export to PDF'\n"
        "  B) VS Code:   Install 'yzane.markdown-pdf' -> right-click -> export\n"
        "  C) Browser:   python -m http.server 8000\n"
        "                open http://localhost:8000/README_Gold.md -> Print -> PDF\n"
        "  D) Install:   pip install markdown weasyprint  (needs GTK on Windows)\n"
        "     Then:      python export_pdf.py\n"
        "  -------------------------------------------------\n"
    )
    sys.stdout.buffer.write(msg.encode("utf-8", errors="replace"))
    return False


# ---------------------------------------------------------------------------
# Diagram renderer / test
# ---------------------------------------------------------------------------

def test_render_diagram(md_text: str) -> dict:
    """
    Verify that the ASCII architecture diagram in README_Gold.md renders correctly.

    Checks:
      1. Diagram block exists (fenced ``` or indented 4-space)
      2. All expected component names appear
      3. Connection symbols present (=, -, |, v, +)
      4. No truncated lines (all box lines close)
    """
    results: dict = {"passed": [], "failed": []}

    def chk(label: str, cond: bool, detail: str = "") -> None:
        entry = label + (f" ({detail})" if detail else "")
        (results["passed"] if cond else results["failed"]).append(entry)

    # 1. Code block containing diagram
    diagram_match = re.search(r"```\s*\n([\s\S]+?)\n```", md_text)
    chk("ASCII diagram block found", diagram_match is not None)

    if diagram_match:
        diagram = diagram_match.group(1)

        # 2. Component names
        components = [
            "EXTERNAL WORLD", "WATCHERS", "MCP SERVERS",
            "VAULT", "RALPH", "LOOP", "WATCHDOG",
            "AUDIT", "RESILIENCE",
        ]
        for comp in components:
            chk(f"Component '{comp}' in diagram", comp in diagram)

        # 3. Box-drawing / ASCII art characters present
        for sym, label in [("═", "double-line"), ("║", "vertical"),
                            ("╔", "top-left corner"), ("╚", "bottom-left corner"),
                            ("▼", "arrow")]:
            chk(f"Symbol '{label}' present", sym in diagram)

        # 4. Line length check (no obviously truncated lines > 90 chars without closing)
        long_unclosed = [
            ln for ln in diagram.splitlines()
            if len(ln) > 90 and not ln.rstrip().endswith(("║", "╝", "╗"))
        ]
        chk("No unexpectedly long unclosed lines",
            len(long_unclosed) == 0,
            f"{len(long_unclosed)} found" if long_unclosed else "")

    # 5. Data flow section exists
    flow_match = re.search(r"Data Flow", md_text, re.IGNORECASE)
    chk("Data flow section present", flow_match is not None)

    # 6. Lesson headings present
    lessons = [
        "Ralph loops reduced lazy agent issues",
        "HITL gates must be in the filesystem",
        "Structured NDJSON beats append-only",
    ]
    for lesson in lessons:
        chk(f"Lesson '{lesson[:40]}...' present",
            lesson.lower() in md_text.lower())

    return results


# ---------------------------------------------------------------------------
# Main export function
# ---------------------------------------------------------------------------


def export_pdf(
    input_path:  Path = _VAULT_ROOT / "README_Gold.md",
    output_path: Path | None = None,
) -> Path | None:
    """
    Convert a Markdown file to PDF using the best available backend.
    Returns the output path on success, None on failure.
    """
    if not input_path.exists():
        print(f"[export] ERROR: Input file not found: {input_path}")
        return None

    if output_path is None:
        output_path = _DOCS_DIR / f"{input_path.stem}.pdf"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    md_text = input_path.read_text(encoding="utf-8")
    title   = input_path.stem.replace("_", " ")
    html    = _md_to_html(md_text, title=title)

    backends = [
        ("weasyprint",  lambda: _backend_weasyprint(html, output_path)),
        ("pdfkit",      lambda: _backend_pdfkit(html, output_path)),
        ("reportlab",   lambda: _backend_reportlab(md_text, output_path)),
        ("text_copy",   lambda: _backend_text_copy(md_text, output_path)),
    ]

    for name, fn in backends:
        print(f"[export] Trying backend: {name} ...")
        if fn():
            if output_path.exists():
                size_kb = output_path.stat().st_size // 1024
                print(f"[export] PDF written: {output_path}  ({size_kb} KB)")
                return output_path
            else:
                print(f"[export] Backend '{name}' returned True but file missing.")

    print("[export] All backends failed.")
    return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _check_backends() -> None:
    print("Backend availability:")
    checks = {
        "markdown":   "import markdown",
        "weasyprint": "import weasyprint",
        "pdfkit":     "import pdfkit",
        "reportlab":  "from reportlab.lib.pagesizes import A4",
    }
    for name, stmt in checks.items():
        try:
            exec(stmt)
            print(f"  {name:<12} OK")
        except (ImportError, OSError, Exception) as exc:
            reason = "NOT INSTALLED" if isinstance(exc, ImportError) else f"UNAVAILABLE ({type(exc).__name__})"
            print(f"  {name:<12} {reason}  (pip install {name})")

    # Check pandoc / wkhtmltopdf
    for bin_name in ("pandoc", "wkhtmltopdf"):
        try:
            result = subprocess.run([bin_name, "--version"],
                                    capture_output=True, timeout=5)
            if result.returncode == 0:
                v = result.stdout.decode(errors="ignore").splitlines()[0]
                print(f"  {bin_name:<12} OK  ({v})")
            else:
                print(f"  {bin_name:<12} NOT FOUND")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            print(f"  {bin_name:<12} NOT FOUND")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export Markdown documentation to PDF"
    )
    parser.add_argument("--input",  default=str(_VAULT_ROOT / "README_Gold.md"),
                        help="Input Markdown file (default: README_Gold.md)")
    parser.add_argument("--output", default=None,
                        help="Output PDF path (default: Docs/{input_stem}.pdf)")
    parser.add_argument("--check",  action="store_true",
                        help="Check available PDF backends and exit.")
    parser.add_argument("--test-diagram", action="store_true",
                        help="Validate the README architecture diagram and exit.")
    args = parser.parse_args()

    if args.check:
        _check_backends()
        return

    input_path  = Path(args.input)
    output_path = Path(args.output) if args.output else None

    if args.test_diagram:
        if not input_path.exists():
            print(f"ERROR: {input_path} not found")
            sys.exit(1)
        md_text = input_path.read_text(encoding="utf-8")
        results = test_render_diagram(md_text)
        passed  = len(results["passed"])
        failed  = len(results["failed"])
        for p in results["passed"]:
            print(f"  [PASS]  {p}")
        for f in results["failed"]:
            print(f"  [FAIL]  {f}")
        print(f"\nDiagram test: {passed}/{passed+failed} passed")
        sys.exit(0 if failed == 0 else 1)

    result = export_pdf(input_path, output_path)
    if result:
        print(f"\nGOLD_DOCS_COMPLETE: {result}")
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
