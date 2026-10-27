"""Small Markdown helpers for deterministic documentation output.

Generated files start with :data:`MARKER` so a later run can delete stale
table pages without touching notes a person added beside them.
"""

from __future__ import annotations

import re

MARKER = "<!-- tmdl-docs generated -->"

_HEADING_ESCAPE = set("\\`*_[]<>")
_LABEL_ESCAPE = set("\\`*_[|]<>")


class Raw:
    """Markdown that is already safe to place in a table cell."""

    def __init__(self, text: str):
        self.text = text

    def __str__(self) -> str:
        return self.text


def slug(*parts: str) -> str:
    """Build an HTML id from one or more display strings."""
    text = "-".join(parts)
    value = re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")
    if not value:
        value = "section"
    if value[0].isdigit():
        value = "id-" + value
    return value


def esc_prose(text: str) -> str:
    """Escape inline Markdown in a paragraph while keeping line breaks."""
    value = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    value = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    value = value.replace("\\", "\\\\")
    for char in "`*_[]":
        value = value.replace(char, "\\" + char)
    return value.replace("\n", "<br>\n")


def _escape(text: str, extra: set[str]) -> str:
    chars = extra | {"\\"}
    return "".join("\\" + char if char in chars else char for char in text)


def esc_heading(text: str) -> str:
    return _escape(text, _HEADING_ESCAPE)


def esc_label(text: str) -> str:
    return _escape(text, _LABEL_ESCAPE)


def esc_cell(value: object) -> str:
    """Escape a table cell. :class:`Raw` values are inserted as Markdown."""
    if isinstance(value, Raw):
        return value.text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")
    if value is None:
        return ""
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = text.replace("\\", "\\\\").replace("|", "\\|")
    for char in "`*_[]":
        text = text.replace(char, "\\" + char)
    return text.replace("\n", "<br>")


def code_span(text: str) -> str:
    """Inline code span. Multiline text is flattened with spaces."""
    value = "" if text is None else str(text).replace("\r\n", "\n").replace("\r", "\n")
    value = value.replace("\n", " ")
    longest = _backtick_run(value)
    fence = "`" * (longest + 1)
    pad = " " if value.startswith("`") or value.endswith("`") else ""
    return f"{fence}{pad}{value}{pad}{fence}"


def html_code(text: str | None) -> str:
    """Code element safe inside a Markdown table cell."""
    if text is None or text == "":
        return ""
    escaped = (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("|", "&#124;")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\n", "<br>")
    )
    return f"<code>{escaped}</code>"


def fenced_code(text: str | None, language: str = "") -> str:
    body = "" if text is None else str(text)
    body = body.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    ticks = max(3, _backtick_run(body) + 1)
    return f"{'`' * ticks}{language}\n{body}\n{'`' * ticks}"


def _backtick_run(text: str) -> int:
    longest = 0
    run = 0
    for char in text:
        if char == "`":
            run += 1
            longest = max(longest, run)
        else:
            run = 0
    return longest


def encode_href(href: str) -> str:
    return href.replace("%", "%25").replace(" ", "%20").replace("(", "%28").replace(")", "%29")


def md_link(label: str, href: str) -> str:
    return f"[{esc_label(label)}]({encode_href(href)})"


def heading(level: int, text: str, anchor: str | None = None) -> str:
    line = f"{'#' * level} {esc_heading(text)}"
    if not anchor:
        return line
    return f'<a id="{anchor}"></a>\n\n{line}'


def bullets(items: list[str]) -> str:
    values = list(items)
    if not values:
        return "None."
    return "\n".join(f"- {item}" for item in values)


def numbered(items: list[str]) -> str:
    values = list(items)
    if not values:
        return "None."
    return "\n".join(f"{index}. {item}" for index, item in enumerate(values, 1))


def markdown_table(headers: list[str], rows: list[list[object]]) -> str:
    if not rows:
        return "None."
    width = len(headers)

    def line(cells: list[object]) -> str:
        values = [esc_cell(cell) for cell in cells]
        if len(values) < width:
            values.extend([""] * (width - len(values)))
        return "| " + " | ".join(values[:width]) + " |"

    separator = "| " + " | ".join("---" for _ in headers) + " |"
    return "\n".join([line(list(headers)), separator, *(line(list(row)) for row in rows)])


def document(blocks: list[str | None]) -> str:
    """Join sections into one generated Markdown file with a trailing newline."""
    parts = [MARKER]
    for block in blocks:
        if block is None:
            continue
        text = str(block).strip("\n")
        if text:
            parts.append(text)
    return "\n\n".join(parts) + "\n"


_FENCE_RE = re.compile(r"^(```|~~~)")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_ANCHOR_RE = re.compile(r"""<a\s+id=["']([^"']+)["']\s*>\s*</a>""")
_ANCHOR_LINE_RE = re.compile(r"""^<a\s+id=["']([^"']+)["']\s*>\s*</a>\s*$""")


def github_heading_slug(text: str) -> str:
    """Slug a heading the way GitHub and VS Code do.

    Punctuation is removed and spaces become hyphens. Underscores stay.
    ``Gate/CP`` becomes ``gatecp``, and spaces around a hyphen stay as
    separate hyphens (``Portfolio - Gate6/CP5`` becomes ``portfolio---gate6cp5``).
    """
    plain = text.replace("\\", "")
    plain = re.sub(r"`+", "", plain).strip().lower()
    plain = re.sub(r"[^\w\s-]", "", plain, flags=re.ASCII)
    return re.sub(r"\s", "-", plain)


def _heading_slugs(lines: list[str], fenced: set[int]) -> list[tuple[int, str]]:
    """Heading line indexes paired with unique GitHub slugs, in document order."""
    counts: dict[str, int] = {}
    found: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        if index in fenced:
            continue
        match = _HEADING_RE.match(line)
        if not match:
            continue
        base = github_heading_slug(match.group(2))
        if not base:
            continue
        seen = counts.get(base)
        if seen is None:
            counts[base] = 0
            slug = base
        else:
            counts[base] = seen + 1
            slug = github_heading_slug(f"{base}-{counts[base]}")
        found.append((index, slug))
    return found


def _fenced_lines(lines: list[str]) -> set[int]:
    fenced: set[int] = set()
    in_fence = False
    for index, line in enumerate(lines):
        if _FENCE_RE.match(line.strip()):
            in_fence = not in_fence
            fenced.add(index)
            continue
        if in_fence:
            fenced.add(index)
    return fenced


def _next_content(lines: list[str], start: int, fenced: set[int]) -> int | None:
    for index in range(start, len(lines)):
        if index in fenced or not lines[index].strip():
            continue
        return index
    return None


def _fragment_map(text: str) -> dict[str, str]:
    """Map an explicit anchor id to the heading slug a reader can actually open."""
    lines = text.splitlines()
    fenced = _fenced_lines(lines)
    headings = _heading_slugs(lines, fenced)
    heading_at = {index: slug for index, slug in headings}
    mapping: dict[str, str] = {}
    previous = ""
    for index, line in enumerate(lines):
        if index in fenced:
            continue
        if index in heading_at:
            previous = heading_at[index]
        for anchor in _ANCHOR_RE.findall(line):
            if _ANCHOR_LINE_RE.match(line.strip()):
                nxt = _next_content(lines, index + 1, fenced)
                if nxt is not None and nxt in heading_at:
                    mapping[anchor] = heading_at[nxt]
                    continue
            if previous:
                mapping[anchor] = previous
    return mapping


def _parse_link_spans(line: str) -> list[tuple[int, int, str]]:
    """Return ``(start, end, destination)`` for CommonMark inline links."""
    found: list[tuple[int, int, str]] = []
    index = 0
    length = len(line)
    while index < length:
        if line[index] == "\\" and index + 1 < length:
            index += 2
            continue
        if line[index] != "[":
            index += 1
            continue
        cursor = index + 1
        depth = 1
        while cursor < length and depth:
            if line[cursor] == "\\" and cursor + 1 < length:
                cursor += 2
                continue
            if line[cursor] == "[":
                depth += 1
            elif line[cursor] == "]":
                depth -= 1
                if depth == 0:
                    break
            cursor += 1
        if depth == 0 and cursor + 1 < length and line[cursor + 1] == "(":
            dest_start = cursor + 2
            dest_end = dest_start
            dest: list[str] = []
            while dest_end < length and line[dest_end] not in ")\n" and not line[dest_end].isspace():
                if line[dest_end] == "\\" and dest_end + 1 < length:
                    dest.append(line[dest_end + 1])
                    dest_end += 2
                    continue
                dest.append(line[dest_end])
                dest_end += 1
            if dest_end < length and line[dest_end] == ")":
                found.append((dest_start, dest_end, "".join(dest)))
                index = dest_end + 1
                continue
        index += 1
    return found


def _rewrite_anchor_ids(text: str, mapping: dict[str, str]) -> str:
    """Point heading anchors at the same slug the heading itself receives."""
    lines = text.splitlines()
    fenced = _fenced_lines(lines)
    headings = _heading_slugs(lines, fenced)
    heading_at = {index: slug for index, slug in headings}
    rewritten: list[str] = []
    for index, line in enumerate(lines):
        if index in fenced:
            rewritten.append(line)
            continue
        match = _ANCHOR_LINE_RE.match(line.strip())
        if match:
            nxt = _next_content(lines, index + 1, fenced)
            if nxt is not None and nxt in heading_at:
                slug = heading_at[nxt]
                if slug and slug != match.group(1):
                    line = f'<a id="{slug}"></a>'
        rewritten.append(line)

    # Section headings that links now target, but that have no explicit anchor yet.
    needed = set(mapping.values())
    lines = rewritten
    fenced = _fenced_lines(lines)
    headings = _heading_slugs(lines, fenced)
    output: list[str] = []
    for index, line in enumerate(lines):
        slug = dict(headings).get(index)
        if slug and slug in needed:
            previous = _previous_content(lines, index, fenced)
            already = previous is not None and _ANCHOR_LINE_RE.match(lines[previous].strip())
            if already and _ANCHOR_LINE_RE.match(lines[previous].strip()).group(1) == slug:
                output.append(line)
                continue
            if output and output[-1].strip():
                output.append("")
            output.append(f'<a id="{slug}"></a>')
            output.append("")
        output.append(line)
    body = "\n".join(output)
    if text.endswith("\n"):
        body += "\n"
    return body


def _previous_content(lines: list[str], start: int, fenced: set[int]) -> int | None:
    for index in range(start - 1, -1, -1):
        if index in fenced or not lines[index].strip():
            continue
        return index
    return None


def retarget_heading_links(files: dict[str, str], filename: str = "Measures.md") -> dict[str, str]:
    """Point links at a heading's line, not its slug.

    Cursor opens a Markdown link by file and only honors a ``#L123`` fragment.
    A heading slug opens the file at the top. Same-page slug links have the
    same problem. Each heading anchor on ``filename`` becomes ``L<line>``, and
    every link that used the old slug is rewritten to that line.
    """
    text = files.get(filename)
    if text is None:
        return files
    lines = text.splitlines()
    fenced = _fenced_lines(lines)
    id_to_line: dict[str, int] = {}
    rewritten: list[str] = []
    for index, line in enumerate(lines):
        if index not in fenced:
            match = _ANCHOR_LINE_RE.match(line.strip())
            if match:
                nxt = _next_content(lines, index + 1, fenced)
                if nxt is not None and nxt not in fenced and _HEADING_RE.match(lines[nxt]):
                    line_no = nxt + 1
                    anchor = match.group(1)
                    if anchor != f"L{line_no}":
                        id_to_line[anchor] = line_no
                        line = f'<a id="L{line_no}"></a>'
        rewritten.append(line)
    updated = dict(files)
    body = "\n".join(rewritten)
    if text.endswith("\n"):
        body += "\n"
    updated[filename] = body
    if not id_to_line:
        return updated
    link = re.compile(r"\]\(([^)#\s]*)#([^)\s]+)\)")

    def replace(match: re.Match[str]) -> str:
        path, frag = match.group(1), match.group(2)
        line_no = id_to_line.get(frag)
        if line_no is None:
            return match.group(0)
        target = path.replace("\\", "/")
        if target and not target.endswith("/" + filename) and target != filename:
            return match.group(0)
        return f"]({path}#L{line_no})"

    result: dict[str, str] = {}
    for name, content in updated.items():
        content_lines = content.splitlines()
        content_fenced = _fenced_lines(content_lines)
        out = [
            line if index in content_fenced else link.sub(replace, line)
            for index, line in enumerate(content_lines)
        ]
        joined = "\n".join(out)
        if content.endswith("\n"):
            joined += "\n"
        result[name] = joined
    return result


def align_link_fragments(files: dict[str, str]) -> dict[str, str]:
    """Retarget Markdown fragments to the heading slug of the destination.

    Generated pages used ids such as ``page-gate-cp``. The heading those ids
    introduce is ``Gate/CP``, whose own anchor is ``gatecp``. Preview navigation
    follows the heading, so the prefixed id never scrolled into view. Anchors
    that sit in a table, with no heading of their own, resolve to the section
    heading that contains the table.
    """
    import posixpath
    from urllib.parse import unquote

    maps = {name: _fragment_map(text) for name, text in files.items()}
    anchored = {name: _rewrite_anchor_ids(text, maps[name]) for name, text in files.items()}

    def locate(source: str, href_path: str) -> str | None:
        if not href_path.lower().endswith(".md"):
            return None
        target = posixpath.normpath(posixpath.join(posixpath.dirname(source), unquote(href_path)))
        if target in maps:
            return target
        return None

    aligned: dict[str, str] = {}
    for name, text in anchored.items():
        lines = text.splitlines()
        fenced = _fenced_lines(lines)
        rewritten: list[str] = []
        for index, line in enumerate(lines):
            if index in fenced or "](" not in line:
                rewritten.append(line)
                continue
            spans = _parse_link_spans(line)
            if not spans:
                rewritten.append(line)
                continue
            cursor = 0
            parts: list[str] = []
            for start, end, dest in spans:
                parts.append(line[cursor:start])
                path_part, sep, frag = dest.partition("#")
                replacement = dest
                if sep:
                    frag = unquote(frag)
                    if path_part:
                        target = locate(name, path_part)
                    else:
                        target = name
                    mapped = maps.get(target or "", {}).get(frag)
                    if target and mapped and mapped != frag:
                        replacement = f"{path_part}#{mapped}"
                parts.append(replacement)
                cursor = end
            parts.append(line[cursor:])
            rewritten.append("".join(parts))
        body = "\n".join(rewritten)
        if text.endswith("\n"):
            body += "\n"
        aligned[name] = body
    return aligned
