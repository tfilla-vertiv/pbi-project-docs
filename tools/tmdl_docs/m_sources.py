"""Physical-source and query-reference extraction from Power Query (M) code.

Credentials are never resolved: TMDL does not store them, and password-like
fragments inside connection strings are redacted from the extracted values.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .model import DataSource

KNOWN_CONNECTORS = {
    "PowerPlatform.Dataflows",
    "PowerBI.Dataflows",
    "Sql.Database",
    "Sql.Databases",
    "Odbc.DataSource",
    "Odbc.Query",
    "Web.Contents",
    "Web.Page",
    "SharePoint.Files",
    "SharePoint.Contents",
    "SharePoint.Tables",
    "File.Contents",
    "Folder.Files",
    "Folder.Contents",
}

FORMAT_FUNCTIONS = {
    "Excel.Workbook",
    "Csv.Document",
    "Json.Document",
    "Xml.Tables",
    "Xml.Document",
    "Parquet.Document",
    "Pdf.Tables",
}

# Standard-library namespaces whose functions transform data rather than read it.
TRANSFORM_NAMESPACES = {
    "Table", "List", "Record", "Text", "Number", "Date", "DateTime", "DateTimeZone",
    "Duration", "Time", "Logical", "Binary", "BinaryFormat", "Byte", "Character",
    "Combiner", "Splitter", "Replacer", "Comparer", "Culture", "Expression",
    "Function", "Type", "Value", "Lines", "Uri", "Order", "JoinKind", "JoinAlgorithm",
    "JoinSide", "MissingField", "Occurrence", "RoundingMode", "QuoteStyle",
    "ExtraValues", "GroupKind", "Precision", "RelativePosition", "TextEncoding",
    "Compression", "Diagnostics", "Error", "Int8", "Int16", "Int32", "Int64",
    "Single", "Double", "Decimal", "Currency", "Percentage", "Guid", "Cube",
    "Variable", "Action", "Json", "Csv", "Xml", "Excel", "Parquet", "Pdf",
    "Day", "Access", "Embedded", "Identity", "LimitClauseKind",
    "Geography", "Geometry", "Graph", "ODataOmitValues", "WebMethod",
}

_SECRET_RE = re.compile(r"(?i)\b(pwd|password|secret|token|apikey|api_key|accountkey)\s*=\s*[^;\"]*")
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")


@dataclass
class MToken:
    kind: str  # "string" | "qident" | "ident" | "number" | "op"
    value: str
    start: int
    end: int


def redact(text: str) -> str:
    return _SECRET_RE.sub(lambda m: f"{m.group(1)}=***", text)


def tokenize(text: str) -> list[MToken]:
    tokens: list[MToken] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if text.startswith("//", i):
            end = text.find("\n", i)
            i = n if end < 0 else end + 1
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        if ch == '"' or text.startswith('#"', i):
            start = i
            i += 2 if ch == "#" else 1
            while i < n:
                if text[i] == '"':
                    if i + 1 < n and text[i + 1] == '"':
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            raw = text[start:i]
            body = raw[2:-1] if ch == "#" else raw[1:-1]
            kind = "qident" if ch == "#" else "string"
            tokens.append(MToken(kind, body.replace('""', '"'), start, i))
            continue
        if ch == "#" and i + 1 < n and text[i + 1].isalpha():
            match = _IDENT_RE.match(text, i + 1)
            tokens.append(MToken("ident", "#" + match.group(0), i, match.end()))
            i = match.end()
            continue
        if ch.isdigit():
            match = re.match(r"\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", text[i:])
            tokens.append(MToken("number", match.group(0), i, i + match.end()))
            i += match.end()
            continue
        match = _IDENT_RE.match(text, i)
        if match:
            tokens.append(MToken("ident", match.group(0), i, match.end()))
            i = match.end()
            continue
        if text.startswith("=>", i) or text.startswith("<>", i) or text.startswith("<=", i) \
                or text.startswith(">=", i) or text.startswith("..", i):
            tokens.append(MToken("op", text[i:i + 2], i, i + 2))
            i += 2
            continue
        tokens.append(MToken("op", ch, i, i + 1))
        i += 1
    return tokens


_OPEN = {"(": ")", "[": "]", "{": "}"}
_CLOSE = {")", "]", "}"}


def _is(tok: MToken | None, kind: str, value: str | None = None) -> bool:
    return tok is not None and tok.kind == kind and (value is None or tok.value == value)


def _name_of(tok: MToken) -> str | None:
    if tok.kind in ("ident", "qident"):
        return tok.value
    return None


def _matching(tokens: list[MToken], open_index: int) -> int:
    """Index of the bracket closing the one at ``open_index`` (or last token)."""
    depth = 0
    for j in range(open_index, len(tokens)):
        tok = tokens[j]
        if tok.kind == "op" and tok.value in _OPEN:
            depth += 1
        elif tok.kind == "op" and tok.value in _CLOSE:
            depth -= 1
            if depth == 0:
                return j
    return len(tokens) - 1


@dataclass
class Step:
    name: str
    tokens: list[MToken]
    text: str


def parse_steps(text: str) -> list[Step]:
    """Split the outermost ``let ... in`` into named steps.

    Expressions that are not a ``let`` block become a single step named after
    the whole expression's result (``<expression>``).
    """
    tokens = tokenize(text)
    if not tokens:
        return []
    start = next((i for i, t in enumerate(tokens) if _is(t, "ident", "let")), None)
    if start is None:
        return [Step("<expression>", tokens, text.strip())]
    steps: list[Step] = []
    depth = 0
    nested_lets = 0
    segment_start = start + 1
    i = start + 1

    def flush(end: int) -> None:
        segment = tokens[segment_start:end]
        if len(segment) >= 2 and _name_of(segment[0]) and _is(segment[1], "op", "="):
            body = segment[2:]
            body_text = text[body[0].start:body[-1].end] if body else ""
            steps.append(Step(segment[0].value, body, body_text))

    while i < len(tokens):
        tok = tokens[i]
        if tok.kind == "op" and tok.value in _OPEN:
            depth += 1
        elif tok.kind == "op" and tok.value in _CLOSE:
            depth -= 1
        elif depth == 0 and _is(tok, "ident", "let"):
            nested_lets += 1
        elif depth == 0 and _is(tok, "ident", "in"):
            if nested_lets:
                nested_lets -= 1
            else:
                flush(i)
                break
        elif depth == 0 and nested_lets == 0 and _is(tok, "op", ","):
            flush(i)
            segment_start = i + 1
        i += 1
    return steps


def _split_args(tokens: list[MToken], open_index: int, close_index: int) -> list[list[MToken]]:
    args: list[list[MToken]] = []
    current: list[MToken] = []
    depth = 0
    for tok in tokens[open_index + 1:close_index]:
        if tok.kind == "op" and tok.value in _OPEN:
            depth += 1
        elif tok.kind == "op" and tok.value in _CLOSE:
            depth -= 1
        if depth == 0 and _is(tok, "op", ","):
            args.append(current)
            current = []
            continue
        current.append(tok)
    if current or args:
        args.append(current)
    return args


def _arg_text(text: str, arg: list[MToken]) -> str:
    if not arg:
        return ""
    if len(arg) == 1 and arg[0].kind == "string":
        return redact(arg[0].value)
    return redact(text[arg[0].start:arg[-1].end])


def _parse_record(tokens: list[MToken], text: str) -> dict[str, str]:
    """Parse the fields of an M record literal's inner tokens (``a = "x", b = 1``)."""
    fields: dict[str, str] = {}
    depth = 0
    segment: list[MToken] = []
    for tok in tokens + [MToken("op", ",", -1, -1)]:
        if tok.kind == "op" and tok.value in _OPEN:
            depth += 1
        elif tok.kind == "op" and tok.value in _CLOSE:
            depth -= 1
        if depth == 0 and _is(tok, "op", ","):
            if len(segment) >= 3 and _name_of(segment[0]) and _is(segment[1], "op", "="):
                fields[segment[0].value] = _arg_text(text, segment[2:])
            segment = []
            continue
        segment.append(tok)
    return fields


def _parse_navigation(tokens: list[MToken], index: int, text: str) -> tuple[dict[str, str] | None, int]:
    """Parse ``{[k = v, ...]}[Field]`` or ``{0}[Field]`` starting at ``index``."""
    if not _is(tokens[index] if index < len(tokens) else None, "op", "{"):
        return None, index
    close = _matching(tokens, index)
    inner = tokens[index + 1:close]
    nav: dict[str, str] = {}
    if inner and _is(inner[0], "op", "[") and _is(inner[-1], "op", "]"):
        nav.update(_parse_record(inner[1:-1], text))
    elif inner:
        nav["index"] = _arg_text(text, inner)
    j = close + 1
    if j < len(tokens) and _is(tokens[j], "op", "["):
        field_close = _matching(tokens, j)
        field_tokens = tokens[j + 1:field_close]
        if field_tokens:
            nav["field"] = _arg_text(text, field_tokens)
        j = field_close + 1
    return nav, j


@dataclass
class _Call:
    function: str
    step: str
    args: list[list[MToken]]
    open_index: int
    close_index: int
    trailing_navigation: list[dict[str, str]]


def _find_calls(step: Step, text: str) -> list[_Call]:
    calls: list[_Call] = []
    tokens = step.tokens
    for i, tok in enumerate(tokens[:-1]):
        if tok.kind != "ident" or "." not in tok.value or not _is(tokens[i + 1], "op", "("):
            continue
        close = _matching(tokens, i + 1)
        navigation: list[dict[str, str]] = []
        j = close + 1
        while j < len(tokens):
            nav, nxt = _parse_navigation(tokens, j, text)
            if nav is None:
                break
            navigation.append(nav)
            j = nxt
        calls.append(_Call(tok.value, step.name, _split_args(tokens, i + 1, close), i + 1, close, navigation))
    return calls


def _classify(function: str) -> str | None:
    if function in KNOWN_CONNECTORS:
        return "known"
    if function in FORMAT_FUNCTIONS:
        return "format"
    namespace = function.split(".", 1)[0]
    if namespace in TRANSFORM_NAMESPACES:
        return None
    return "generic"


def _details(connector: str, args: list[str], navigation: list[dict[str, str]]) -> dict[str, str]:
    details: dict[str, str] = {}
    first = args[0] if args else ""
    if connector in ("Sql.Database", "Sql.Databases"):
        if args:
            details["server"] = first
        if len(args) > 1 and connector == "Sql.Database":
            details["database"] = args[1]
    elif connector in ("Odbc.DataSource", "Odbc.Query"):
        if args:
            details["connection"] = first
        if len(args) > 1 and connector == "Odbc.Query":
            details["query"] = args[1]
    elif connector in ("Web.Contents", "Web.Page"):
        if args:
            details["url"] = first
    elif connector.startswith("SharePoint."):
        if args:
            details["site"] = first
    elif connector in ("File.Contents", "Folder.Files", "Folder.Contents"):
        if args:
            details["path"] = first
    for nav in navigation:
        for key, value in nav.items():
            if key in ("field", "index"):
                continue
            details[key] = value
    return details


def query_references(text: str, query_names: set[str]) -> list[str]:
    """Names of other queries (tables/shared expressions) referenced by M code."""
    tokens = tokenize(text)
    local = {
        tokens[i].value
        for i in range(len(tokens) - 1)
        if _name_of(tokens[i]) and _is(tokens[i + 1], "op", "=")
    }
    found: set[str] = set()
    record_depth = 0
    for tok in tokens:
        if _is(tok, "op", "["):
            record_depth += 1
        elif _is(tok, "op", "]"):
            record_depth -= 1
        elif record_depth == 0 and _name_of(tok) and tok.value in query_names and tok.value not in local:
            found.add(tok.value)
    return sorted(found)


def extract_sources(text: str, table: str, partition: str) -> list[DataSource]:
    """Derive physical-source records from an M expression."""
    steps = parse_steps(text)
    connector_calls: list[tuple[_Call, str]] = []
    format_calls: list[_Call] = []
    for step in steps:
        for call in _find_calls(step, text):
            category = _classify(call.function)
            if category in ("known", "generic"):
                connector_calls.append((call, category))
            elif category == "format":
                format_calls.append(call)

    # chains: step name -> (connector call index, navigation path)
    chains: dict[str, tuple[int, list[dict[str, str]]]] = {}
    leaves: dict[int, list[list[dict[str, str]]]] = {}
    for idx, (call, _) in enumerate(connector_calls):
        chains.setdefault(call.step, (idx, list(call.trailing_navigation)))
    extended: set[str] = set()
    for step in steps:
        if step.name in chains or not step.tokens:
            continue
        base = _name_of(step.tokens[0])
        if base is None or base not in chains:
            continue
        path: list[dict[str, str]] = []
        j = 1
        while j < len(step.tokens):
            nav, nxt = _parse_navigation(step.tokens, j, text)
            if nav is None:
                break
            path.append(nav)
            j = nxt
        if not path:
            continue
        idx, base_path = chains[base]
        chains[step.name] = (idx, base_path + path)
        extended.add(base)
    for name, (idx, path) in chains.items():
        if name not in extended:
            leaves.setdefault(idx, []).append(path)

    sources: list[DataSource] = []
    sources_by_call: dict[int, list[DataSource]] = {}
    for idx, (call, category) in enumerate(connector_calls):
        args = [_arg_text(text, a) for a in call.args]
        for path in leaves.get(idx, [list(call.trailing_navigation)]):
            source = DataSource(
                connector=call.function,
                table=table,
                partition=partition,
                step=call.step,
                arguments=args,
                navigation=path,
                details=_details(call.function, args, path),
                is_generic=category == "generic",
            )
            sources.append(source)
            sources_by_call.setdefault(idx, []).append(source)

    for call in format_calls:
        targets: list[DataSource] = []
        for idx, (connector, _) in enumerate(connector_calls):
            if connector.step == call.step and call.open_index < connector.open_index <= call.close_index:
                targets.extend(sources_by_call.get(idx, []))
        if not targets and call.args and call.args[0]:
            base = _name_of(call.args[0][0])
            if base in chains:
                targets = sources_by_call.get(chains[base][0], [])
        if targets:
            for source in targets:
                source.format = call.function
            continue
        arg_text = " ".join(t.value for t in (call.args[0] if call.args else []))
        connector = "Embedded data" if "Binary.FromText" in arg_text else call.function
        sources.append(DataSource(
            connector=connector,
            table=table,
            partition=partition,
            step=call.step,
            arguments=[_arg_text(text, a) for a in call.args] if connector == call.function else [],
            format=call.function,
            is_generic=True,
        ))
    return sources
