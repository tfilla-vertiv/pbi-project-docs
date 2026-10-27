"""Generic, indentation-aware parser for TMDL documents.

The parser turns a TMDL file into a tree of :class:`Node` objects without
knowing the semantics of individual object types. Domain mapping happens in
:mod:`tmdl_docs.loader`.

Supported syntax:

* object declarations: ``keyword Name``, ``keyword 'Quoted Name' = expression``
  and ``ref keyword Name``;
* properties: ``name: value``;
* expression properties: ``name = expression`` (for example ``source =``);
* flags: a bare identifier such as ``isHidden``;
* expressions written inline, as deeper-indented continuation lines, or as
  triple-backtick fenced blocks;
* ``///`` description lines preceding an object.
"""

from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass, field

FENCE = "```"

_PROPERTY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s?(.*)$")
_EXPRESSION_PROPERTY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")
_OBJECT_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s+(.+)$")
_FLAG_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)$")


class TmdlSyntaxError(ValueError):
    def __init__(self, message: str, path: str | None = None, line: int | None = None):
        location = f"{path or '<tmdl>'}:{line}" if line else (path or "<tmdl>")
        super().__init__(f"{location}: {message}")
        self.path = path
        self.line = line


@dataclass
class Node:
    """A single TMDL line (object, property, or flag) and its children."""

    keyword: str
    kind: str  # "object" | "property" | "flag"
    line: int
    indent: int
    name: str | None = None
    raw_name: str | None = None
    value: str | None = None
    is_expression: bool = False
    is_ref: bool = False
    description: str | None = None
    children: list[Node] = field(default_factory=list)

    def objects(self, keyword: str | None = None) -> list[Node]:
        return [
            child
            for child in self.children
            if child.kind == "object" and (keyword is None or child.keyword == keyword)
        ]

    def first_object(self, keyword: str) -> Node | None:
        found = self.objects(keyword)
        return found[0] if found else None

    def properties(self) -> dict[str, str]:
        return {
            child.keyword: child.value or ""
            for child in self.children
            if child.kind == "property"
        }

    def prop(self, key: str, default: str | None = None) -> str | None:
        value = default
        for child in self.children:
            if child.kind == "property" and child.keyword == key:
                value = child.value
        return value

    def flags(self) -> list[str]:
        return [child.keyword for child in self.children if child.kind == "flag"]

    def has_flag(self, name: str) -> bool:
        """True when ``name`` appears as a bare flag or as ``name: true``."""
        for child in self.children:
            if child.keyword != name:
                continue
            if child.kind == "flag":
                return True
            if child.kind == "property":
                return (child.value or "").strip().lower() == "true"
        return False


def unquote_name(text: str) -> str:
    """Return the logical name for a possibly single-quoted TMDL identifier."""
    text = text.strip()
    if len(text) >= 2 and text[0] == "'" and text[-1] == "'":
        return text[1:-1].replace("''", "'")
    return text


def quote_name(name: str) -> str:
    """Quote a name the way TMDL serializes it when it is not a simple identifier."""
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        return name
    return "'" + name.replace("'", "''") + "'"


def _read_quoted(text: str, start: int) -> int:
    """Return the index just past the closing quote of a quoted name at ``start``."""
    i = start + 1
    while i < len(text):
        if text[i] == "'":
            if i + 1 < len(text) and text[i + 1] == "'":
                i += 2
                continue
            return i + 1
        i += 1
    raise ValueError(f"unterminated quoted name: {text[start:]}")


def split_declaration(rest: str) -> tuple[str, str, str | None]:
    """Split ``Name = expression`` into (name, raw_name, expression-or-None)."""
    rest = rest.strip()
    if rest.startswith("'"):
        end = _read_quoted(rest, 0)
        raw = rest[:end]
        remainder = rest[end:].strip()
    else:
        raw, sep, tail = rest.partition("=")
        raw = raw.strip()
        remainder = sep + tail
    value = None
    if remainder.startswith("="):
        value = remainder[1:]
    elif remainder:
        raise ValueError(f"unexpected text after name: {remainder!r}")
    return unquote_name(raw), raw, value


def split_qualified(text: str) -> list[str]:
    """Split ``'Table Name'.Column`` style references into unquoted parts."""
    text = text.strip()
    parts: list[str] = []
    i = 0
    current_start = 0
    while i < len(text):
        ch = text[i]
        if ch == "'" and i == current_start:
            i = _read_quoted(text, i)
            continue
        if ch == ".":
            parts.append(unquote_name(text[current_start:i]))
            current_start = i + 1
        i += 1
    parts.append(unquote_name(text[current_start:]))
    return parts


class _Lines:
    def __init__(self, text: str):
        self.lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        self.space_unit = self._detect_space_unit()

    def _detect_space_unit(self) -> int:
        for line in self.lines:
            if line.startswith(" ") and line.strip():
                return max(1, len(line) - len(line.lstrip(" ")))
        return 4

    def indent(self, index: int) -> int:
        line = self.lines[index]
        tabs = len(line) - len(line.lstrip("\t"))
        if tabs:
            return tabs
        spaces = len(line) - len(line.lstrip(" "))
        return spaces // self.space_unit

    def is_blank(self, index: int) -> bool:
        return not self.lines[index].strip()


def _clean_block(lines: list[str]) -> str:
    text = textwrap.dedent("\n".join(lines))
    return text.strip("\n").rstrip()


def _read_expression(
    src: _Lines, index: int, decl_indent: int, rhs: str, path: str | None
) -> tuple[str, int]:
    """Read an expression starting on line ``index``; return (text, next_index)."""
    first = rhs.strip()
    if first.startswith(FENCE):
        body: list[str] = []
        inline = first[len(FENCE):]
        if inline.strip():
            body.append(inline)
        i = index + 1
        while i < len(src.lines):
            if src.lines[i].strip().startswith(FENCE):
                return _clean_block(body), i + 1
            body.append(src.lines[i])
            i += 1
        raise TmdlSyntaxError("unterminated ``` expression block", path, index + 1)

    i = index + 1
    end = i
    while i < len(src.lines):
        if src.is_blank(i):
            i += 1
            continue
        if src.indent(i) < decl_indent + 2:
            break
        i += 1
        end = i
    block = _clean_block(src.lines[index + 1:end])
    if first:
        return (first + "\n" + block if block else first), end
    return block, end


def parse_tmdl(text: str, path: str | None = None) -> list[Node]:
    """Parse TMDL text into top-level nodes."""
    src = _Lines(text)
    roots: list[Node] = []
    stack: list[Node] = []
    pending_description: list[str] = []
    i = 0
    while i < len(src.lines):
        if src.is_blank(i):
            i += 1
            continue
        indent = src.indent(i)
        content = src.lines[i].strip()
        if content.startswith("///"):
            pending_description.append(content[3:].strip())
            i += 1
            continue
        while stack and stack[-1].indent >= indent:
            stack.pop()
        line_no = i + 1
        next_index = i + 1
        try:
            node, next_index = _parse_line(src, i, indent, content, path)
        except ValueError as exc:
            if isinstance(exc, TmdlSyntaxError):
                raise
            raise TmdlSyntaxError(str(exc), path, line_no) from exc
        if pending_description:
            node.description = "\n".join(pending_description)
            pending_description = []
        (stack[-1].children if stack else roots).append(node)
        stack.append(node)
        i = next_index
    return roots


def _parse_line(
    src: _Lines, index: int, indent: int, content: str, path: str | None
) -> tuple[Node, int]:
    line_no = index + 1
    match = _PROPERTY_RE.match(content)
    if match:
        return (
            Node(keyword=match.group(1), kind="property", line=line_no, indent=indent,
                 value=match.group(2).strip()),
            index + 1,
        )
    match = _EXPRESSION_PROPERTY_RE.match(content)
    if match:
        value, next_index = _read_expression(src, index, indent, match.group(2), path)
        return (
            Node(keyword=match.group(1), kind="property", line=line_no, indent=indent,
                 value=value, is_expression=True),
            next_index,
        )
    match = _OBJECT_RE.match(content)
    if match:
        keyword, rest = match.group(1), match.group(2)
        is_ref = False
        if keyword == "ref":
            ref_match = _OBJECT_RE.match(rest.strip())
            if not ref_match:
                raise ValueError(f"malformed ref declaration: {content!r}")
            keyword, rest = ref_match.group(1), ref_match.group(2)
            is_ref = True
        name, raw_name, rhs = split_declaration(rest)
        node = Node(keyword=keyword, kind="object", line=line_no, indent=indent,
                    name=name, raw_name=raw_name, is_ref=is_ref)
        next_index = index + 1
        if rhs is not None:
            node.value, next_index = _read_expression(src, index, indent, rhs, path)
            node.is_expression = True
        return node, next_index
    match = _FLAG_RE.match(content)
    if match:
        return Node(keyword=match.group(1), kind="flag", line=line_no, indent=indent), index + 1
    raise ValueError(f"unrecognized TMDL line: {content!r}")
