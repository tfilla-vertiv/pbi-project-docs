"""Export the Markdown files in ``docs`` to a separate HTML folder.

The Markdown files are left in place. Links that point at those files are
rewritten so the HTML set can be opened or zipped on its own.

From the package folder, after Markdown has been generated::

    python tools/export_html.py
"""

from __future__ import annotations

import argparse
import html
import os
import re
import sys
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from markdown_it import MarkdownIt

from discover import ProjectError, discover_project

HREF_RE = re.compile(r'href="([^"]*)"')
TITLE_RE = re.compile(r"<h1>(.*?)</h1>", re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")

STYLES = """
:root {
  color-scheme: light;
  --text: #1c1c1c;
  --muted: #5c6570;
  --line: #d8dee4;
  --fill: #f6f8fa;
  --link: #0b57d0;
  --code: #f3f4f6;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  color: var(--text);
  background: #fff;
  font: 16px/1.5 "Segoe UI", system-ui, sans-serif;
}
main {
  max-width: 1100px;
  margin: 0 auto;
  padding: 2rem 1.5rem 3rem;
}
h1, h2, h3, h4 {
  line-height: 1.25;
  margin: 1.6rem 0 0.6rem;
}
h1 {
  margin-top: 0.4rem;
  padding-bottom: 0.35rem;
  border-bottom: 1px solid var(--line);
  font-size: 1.8rem;
}
h2 { font-size: 1.35rem; }
h3 { font-size: 1.12rem; }
p, ul, ol { margin: 0.7rem 0; }
a { color: var(--link); }
a:hover { text-decoration: underline; }
code {
  font-family: Consolas, "Cascadia Mono", ui-monospace, monospace;
  font-size: 0.9em;
  background: var(--code);
  padding: 0.1em 0.35em;
  border-radius: 3px;
}
pre {
  background: var(--fill);
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 0.85rem 1rem;
  overflow-x: auto;
}
pre code {
  background: transparent;
  padding: 0;
  font-size: 0.86rem;
}
.table-wrap {
  overflow-x: auto;
  margin: 1rem 0;
}
table {
  border-collapse: collapse;
  width: 100%;
  font-size: 0.92rem;
}
th, td {
  border: 1px solid var(--line);
  padding: 0.4rem 0.6rem;
  vertical-align: top;
  text-align: left;
}
th { background: var(--fill); }
td, th { overflow-wrap: break-word; }
p:has(> a[id]:only-child) {
  margin: 0;
  height: 0;
}
@media print {
  main { max-width: none; padding: 0; }
  a { color: inherit; text-decoration: none; }
  pre, .table-wrap { break-inside: avoid; }
}
""".strip()


def _renderer() -> MarkdownIt:
    renderer = MarkdownIt(
        "commonmark",
        {"html": True, "linkify": False, "typographer": False, "breaks": False},
    )
    renderer.enable("table")
    # Documentation links use file:// URLs. markdown-it blocks that scheme
    # unless validation is opened up; the hrefs are rewritten after render.
    renderer.validateLink = lambda _url: True
    return renderer


def _file_url_path(href: str) -> tuple[Path, str]:
    parts = urlsplit(href)
    path = unquote(parts.path)
    netloc = parts.netloc
    if netloc and re.fullmatch(r"[A-Za-z]:?", netloc):
        drive = netloc if netloc.endswith(":") else netloc + ":"
        path = drive + path
    elif re.match(r"^/[A-Za-z]:/", path):
        path = path[1:]
    fragment = f"#{parts.fragment}" if parts.fragment else ""
    return Path(path), fragment


def _quote_relative(path: Path) -> str:
    return "/".join(quote(part) for part in path.parts)


def _relative_href(target: Path, source_md: Path, source_root: Path, output_root: Path, fragment: str) -> str:
    output_html = output_root / source_md.relative_to(source_root).with_suffix(".html")
    try:
        inside_docs = target.resolve().relative_to(source_root.resolve())
    except ValueError:
        inside_docs = None

    if inside_docs is not None and inside_docs.suffix.lower() == ".md":
        destination = output_root / inside_docs.with_suffix(".html")
    elif inside_docs is not None:
        destination = output_root / inside_docs
    else:
        destination = target

    if destination.resolve() == output_html.resolve():
        return fragment or output_html.name

    relative = Path(os.path.relpath(destination, output_html.parent))
    return _quote_relative(relative) + fragment


def rewrite_href(href: str, source_md: Path, source_root: Path, output_root: Path) -> str:
    """Point in-docs Markdown links at the matching HTML file."""
    raw = html.unescape(href).strip()
    if not raw or raw.startswith("#"):
        return href

    parts = urlsplit(raw)
    if parts.scheme in {"http", "https", "mailto"}:
        return href
    if parts.scheme == "file":
        target, fragment = _file_url_path(raw)
        rewritten = _relative_href(target, source_md, source_root, output_root, fragment)
        return html.escape(rewritten, quote=True)
    if parts.scheme:
        return href

    fragment = f"#{parts.fragment}" if parts.fragment else ""
    if not parts.path:
        return href
    target = (source_md.parent / unquote(parts.path)).resolve()
    rewritten = _relative_href(target, source_md, source_root, output_root, fragment)
    return html.escape(rewritten, quote=True)


def _page_title(body: str, source_md: Path) -> str:
    match = TITLE_RE.search(body)
    if not match:
        return source_md.stem
    text = html.unescape(TAG_RE.sub("", match.group(1))).strip()
    return text or source_md.stem


def render_page(markdown: str, source_md: Path, source_root: Path, output_root: Path) -> str:
    body = _renderer().render(markdown)
    body = HREF_RE.sub(
        lambda match: f'href="{rewrite_href(match.group(1), source_md, source_root, output_root)}"',
        body,
    )
    body = body.replace("<table>", '<div class="table-wrap"><table>').replace(
        "</table>", "</table></div>"
    )
    title = html.escape(_page_title(body, source_md))
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{title}</title>\n"
        f"<style>\n{STYLES}\n</style>\n"
        "</head>\n"
        "<body>\n"
        "<main>\n"
        f"{body}"
        "</main>\n"
        "</body>\n"
        "</html>\n"
    )


def export_html(source_root: Path, output_root: Path) -> int:
    source_root = source_root.resolve()
    output_root = output_root.resolve()
    if output_root == source_root or source_root in output_root.parents:
        raise SystemExit("The HTML folder must be separate from the Markdown docs folder.")

    written = 0
    for source_md in sorted(source_root.rglob("*.md")):
        markdown = source_md.read_text(encoding="utf-8")
        page = render_page(markdown, source_md, source_root, output_root)
        destination = output_root / source_md.relative_to(source_root).with_suffix(".html")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(page, encoding="utf-8", newline="\n")
        written += 1
    return written


def _resolve(path: Path) -> Path:
    return path.resolve() if path.is_absolute() else (Path.cwd() / path).resolve()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export docs Markdown files to HTML.")
    parser.add_argument("--source-dir", type=Path, help="Markdown folder. Defaults to docs/ beside the .pbip.")
    parser.add_argument("--output-dir", type=Path, help="HTML folder. Defaults to docs-html/ beside the .pbip.")
    args = parser.parse_args(argv)

    project = None
    if args.source_dir is None or args.output_dir is None:
        try:
            project = discover_project()
        except ProjectError as exc:
            if args.source_dir is None:
                print(f"error: {exc}", file=sys.stderr)
                return 1

    if args.source_dir is not None:
        source = _resolve(args.source_dir)
    elif project is not None:
        source = project.root / "docs"
    else:
        source = (Path.cwd() / "docs").resolve()

    if args.output_dir is not None:
        output = _resolve(args.output_dir)
    elif project is not None:
        output = project.root / "docs-html"
    else:
        output = source.parent / "docs-html"

    if not source.is_dir():
        print(f"Source folder not found: {source}", file=sys.stderr)
        return 1

    count = export_html(source, output)
    print(f"Wrote {count} HTML files to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
