"""Quote- and bracket-aware DAX reference extraction and resolution."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .model import (
    REF_AMBIGUOUS,
    REF_LOCAL,
    REF_RESOLVED,
    REF_UNRESOLVED,
    Column,
    DaxReference,
    Measure,
    Table,
)

CONTEXT_MEASURE = "measure"
CONTEXT_COLUMN = "column"
CONTEXT_TABLE = "table"

_KEYWORDS = {
    "VAR", "RETURN", "IN", "NOT", "AND", "OR", "TRUE", "FALSE", "DEFINE",
    "EVALUATE", "ORDER", "BY", "ASC", "DESC", "START", "AT", "MEASURE",
    "COLUMN", "TABLE", "FUNCTION",
}

_IDENT_RE = re.compile(r"[^\W\d]\w*(?:\.\w+)*")
_NUMBER_RE = re.compile(r"\d+(?:\.\d*)?(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?")


@dataclass
class Token:
    kind: str  # "string" | "qtable" | "bracket" | "ident" | "number" | "op"
    value: str
    raw: str
    pos: int


def _read_delimited(text: str, start: int, close: str) -> int:
    """Return index just past ``close`` starting after the opener at ``start``.

    A doubled closing character is an escape. Unterminated input consumes the
    rest of the text rather than raising, so malformed DAX still yields a
    best-effort scan.
    """
    i = start + 1
    while i < len(text):
        if text[i] == close:
            if i + 1 < len(text) and text[i + 1] == close:
                i += 2
                continue
            return i + 1
        i += 1
    return len(text)


def tokenize(text: str) -> list[Token]:
    """Tokenize DAX, dropping whitespace and comments."""
    tokens: list[Token] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if text.startswith("--", i) or text.startswith("//", i):
            end = text.find("\n", i)
            i = n if end < 0 else end + 1
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        if ch == '"':
            end = _read_delimited(text, i, '"')
            raw = text[i:end]
            tokens.append(Token("string", raw[1:-1].replace('""', '"'), raw, i))
            i = end
            continue
        if ch == "'":
            end = _read_delimited(text, i, "'")
            raw = text[i:end]
            tokens.append(Token("qtable", raw[1:-1].replace("''", "'"), raw, i))
            i = end
            continue
        if ch == "[":
            end = _read_delimited(text, i, "]")
            raw = text[i:end]
            tokens.append(Token("bracket", raw[1:-1].replace("]]", "]"), raw, i))
            i = end
            continue
        match = _NUMBER_RE.match(text, i)
        if match and (ch.isdigit() or ch == "."):
            tokens.append(Token("number", match.group(0), match.group(0), i))
            i = match.end()
            continue
        match = _IDENT_RE.match(text, i)
        if match:
            tokens.append(Token("ident", match.group(0), match.group(0), i))
            i = match.end()
            continue
        tokens.append(Token("op", ch, ch, i))
        i += 1
    return tokens


class ModelIndex:
    """Case-insensitive lookups over tables, columns and measures."""

    def __init__(self, tables: list[Table]):
        self.tables: dict[str, Table] = {t.name.casefold(): t for t in tables}
        self.measures: dict[str, list[Measure]] = {}
        self.columns: dict[str, list[Column]] = {}
        for table in tables:
            for measure in table.measures:
                self.measures.setdefault(measure.name.casefold(), []).append(measure)
            for column in table.columns:
                self.columns.setdefault(column.name.casefold(), []).append(column)

    def table(self, name: str) -> Table | None:
        return self.tables.get(name.casefold())


def _is_op(tokens: list[Token], index: int, value: str) -> bool:
    return 0 <= index < len(tokens) and tokens[index].kind == "op" and tokens[index].value == value


def _local_column_names(tokens: list[Token]) -> set[str]:
    """Names that unqualified ``[x]`` may legitimately bind to inside the expression.

    String literals cover virtual columns created by ``ADDCOLUMNS``,
    ``SELECTCOLUMNS``, ``SUMMARIZE`` and similar; implicit names cover table
    functions whose result column names are fixed.
    """
    names = {t.value.casefold() for t in tokens if t.kind == "string"}
    functions = {t.value.upper() for t in tokens if t.kind == "ident"}
    if functions & {"CALENDAR", "CALENDARAUTO"}:
        names.add("date")
    if "GENERATESERIES" in functions or any(t.kind == "op" and t.value == "{" for t in tokens):
        names.add("value")
        names.update(f"value{i}" for i in range(1, 10))
    return names


def _variable_names(tokens: list[Token]) -> set[str]:
    return {
        tokens[i + 1].value.casefold()
        for i, tok in enumerate(tokens[:-1])
        if tok.kind == "ident" and tok.value.upper() == "VAR" and tokens[i + 1].kind == "ident"
    }


def _resolve_qualified(index: ModelIndex, raw: str, table_name: str, member: str) -> DaxReference:
    table = index.table(table_name)
    if table is None:
        return DaxReference(raw, "unknown", REF_UNRESOLVED, table_name, member,
                            note="table not found in model")
    column = table.column(member)
    if column is not None:
        return DaxReference(raw, "column", REF_RESOLVED, table.name, column.name)
    measure = table.measure(member)
    if measure is not None:
        return DaxReference(raw, "measure", REF_RESOLVED, table.name, measure.name)
    elsewhere = index.measures.get(member.casefold(), [])
    return DaxReference(
        raw, "unknown", REF_UNRESOLVED, table.name, member,
        candidates=[m.ref for m in elsewhere],
        note=("measure is hosted on a different table" if elsewhere
              else f"no column or measure named [{member}] on table"),
    )


def _resolve_unqualified(
    index: ModelIndex, raw: str, member: str, owner: str, context: str, local_names: set[str]
) -> DaxReference:
    key = member.casefold()
    measures = index.measures.get(key, [])
    own_measures = [m for m in measures if m.table.casefold() == owner.casefold()]
    if len(own_measures) == 1:
        return DaxReference(raw, "measure", REF_RESOLVED, own_measures[0].table, own_measures[0].name)
    if len(measures) == 1:
        return DaxReference(raw, "measure", REF_RESOLVED, measures[0].table, measures[0].name)
    if len(measures) > 1:
        return DaxReference(raw, "measure", REF_AMBIGUOUS, None, member,
                            candidates=sorted(m.ref for m in measures),
                            note="measure name defined on multiple tables")

    columns = index.columns.get(key, [])
    own_columns = [c for c in columns if c.table.casefold() == owner.casefold()]
    if context == CONTEXT_COLUMN and own_columns:
        column = own_columns[0]
        return DaxReference(raw, "column", REF_RESOLVED, column.table, column.name,
                            note="unqualified column in row context of owning table")
    if key in local_names:
        return DaxReference(raw, "column", REF_LOCAL, None, member,
                            note="column defined within the expression")
    if context == CONTEXT_MEASURE and own_columns and len(columns) == len(own_columns):
        column = own_columns[0]
        return DaxReference(raw, "column", REF_RESOLVED, column.table, column.name,
                            note="unqualified column resolved against owning table")
    if columns:
        return DaxReference(raw, "column", REF_AMBIGUOUS, None, member,
                            candidates=sorted(c.ref for c in columns),
                            note="unqualified column; iterated table cannot be determined statically")
    return DaxReference(raw, "unknown", REF_UNRESOLVED, None, member,
                        note="no measure or column with this name")


def analyze_dax(expression: str | None, owner: str, context: str, index: ModelIndex) -> list[DaxReference]:
    """Extract and resolve references from a DAX expression.

    ``owner`` is the table hosting the measure, calculated column, or
    calculated-table partition; ``context`` is one of ``CONTEXT_*``.
    """
    if not expression:
        return []
    tokens = tokenize(expression)
    variables = _variable_names(tokens)
    local_names = _local_column_names(tokens)
    refs: list[DaxReference] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        if tok.kind == "ident" and (_is_op(tokens, i + 1, "(") or tok.value.upper() in _KEYWORDS):
            i += 1
            continue
        if tok.kind in ("qtable", "ident") and nxt is not None and nxt.kind == "bracket":
            is_table = tok.kind == "qtable" or index.table(tok.value) is not None
            if not is_table and tok.value.casefold() in variables:
                i += 2
                continue
            raw = tok.raw + nxt.raw
            consumed = 2
            if _is_op(tokens, i + 2, ".") and i + 3 < len(tokens) and tokens[i + 3].kind == "bracket":
                raw += "." + tokens[i + 3].raw
                consumed = 4
            ref = _resolve_qualified(index, raw, tok.value, nxt.value)
            if consumed == 4 and ref.note is None:
                ref.note = "includes date-variation suffix"
            refs.append(ref)
            i += consumed
            continue
        if tok.kind == "qtable" or (
            tok.kind == "ident"
            and tok.value.casefold() not in variables
            and index.table(tok.value) is not None
        ):
            table = index.table(tok.value)
            if table is not None:
                refs.append(DaxReference(tok.raw, "table", REF_RESOLVED, table.name))
            else:
                refs.append(DaxReference(tok.raw, "table", REF_UNRESOLVED, tok.value,
                                         note="table not found in model"))
            i += 1
            continue
        if tok.kind == "bracket":
            refs.append(_resolve_unqualified(index, tok.raw, tok.value, owner, context, local_names))
        i += 1
    return dedupe_references(refs)


def dedupe_references(refs: list[DaxReference]) -> list[DaxReference]:
    seen: dict[tuple, DaxReference] = {}
    for ref in refs:
        key = (ref.kind, ref.status, (ref.table or "").casefold(), (ref.name or "").casefold())
        seen.setdefault(key, ref)
    return sorted(
        seen.values(),
        key=lambda r: (r.status, r.kind, (r.table or "").casefold(), (r.name or "").casefold()),
    )
