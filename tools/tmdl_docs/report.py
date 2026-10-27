"""Parse a PBIR report definition into visual field bindings.

Each data visual records the tables, columns, and measures it uses and the
role of each one (rows, columns, values, x-axis, legend, filter, and so on).
Text boxes, images, shapes, buttons, and groups are counted on their report
page and do not become their own documentation page.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .naming import safe_stem

class ReportSyntaxError(Exception):
    """A report JSON file could not be parsed."""


# QueryAggregateFunction values used by PBIR visual queries.
# 0 Sum, 1 Average, 2 Count, 3 Min, 4 Max, 5 Count (non-blank),
# 6 Median, 7 Standard deviation, 8 Variance.
_AGGREGATIONS = {
    0: "Sum",
    1: "Average",
    2: "Count",
    3: "Minimum",
    4: "Maximum",
    5: "Count (non-blank)",
    6: "Median",
    7: "Standard deviation",
    8: "Variance",
}

# Longer prefixes first so CountNonNull is not read as Count.
_QUERY_PREFIXES = (
    ("CountNonNull(", "Count (non-blank)"),
    ("DistinctCount(", "Distinct count"),
    ("StandardDeviation(", "Standard deviation"),
    ("Variance(", "Variance"),
    ("Average(", "Average"),
    ("Median(", "Median"),
    ("Count(", "Count"),
    ("Sum(", "Sum"),
    ("Min(", "Minimum"),
    ("Max(", "Maximum"),
    ("Avg(", "Average"),
)

_GENERIC_ROLES = {
    "Category": "X-axis",
    "Y": "Y-axis",
    "Y2": "Secondary Y-axis",
    "Series": "Legend",
    "Rows": "Rows",
    "Columns": "Columns",
    "Values": "Values",
    "Tooltips": "Tooltips",
    "Size": "Size",
    "X": "X-axis",
    "Legend": "Legend",
    "Details": "Details",
    "Group": "Small multiples",
    "Data": "Data",
    "Goal": "Goal",
    "MinValue": "Minimum value",
    "MaxValue": "Maximum value",
    "TargetValue": "Target value",
    "Indicator": "Indicator",
    "TrendLine": "Trend axis",
    "Play": "Play axis",
    "Gradient": "Color saturation",
    "Latitude": "Latitude",
    "Longitude": "Longitude",
    "Location": "Location",
}

_TYPE_ROLES = {
    "slicer": {"Values": "Field"},
    "advancedSlicerVisual": {"Values": "Field"},
    "listSlicer": {"Values": "Field"},
    "tableEx": {"Values": "Columns"},
    "card": {"Values": "Data"},
    "cardVisual": {"Data": "Data", "Rows": "Small multiples"},
    "scatterChart": {"Category": "Details"},
    "gauge": {"Y": "Value"},
}

_BAR_TYPES = frozenset({
    "barChart",
    "clusteredBarChart",
    "stackedBarChart",
    "hundredPercentStackedBarChart",
})

_SMALL_MULTIPLE_TYPES = frozenset({
    "lineChart",
    "areaChart",
    "stackedAreaChart",
    "hundredPercentStackedAreaChart",
    "columnChart",
    "clusteredColumnChart",
    "stackedColumnChart",
    "hundredPercentStackedColumnChart",
    "barChart",
    "clusteredBarChart",
    "stackedBarChart",
    "hundredPercentStackedBarChart",
    "lineClusteredColumnComboChart",
    "lineStackedColumnComboChart",
    "ribbonChart",
    "waterfallChart",
    "scatterChart",
    "pieChart",
    "donutChart",
    "treemap",
    "funnel",
})

_ROLE_ORDER = (
    "Category", "X", "Y", "Y2", "Series", "Legend", "Rows", "Columns",
    "Values", "Data", "Size", "Tooltips", "Details", "Group", "Play",
    "Gradient", "Latitude", "Longitude", "Location", "Goal", "Indicator",
    "TrendLine", "TargetValue", "MinValue", "MaxValue",
)

_TYPE_LABELS = {
    "pivotTable": "Matrix",
    "tableEx": "Table",
    "cardVisual": "Card",
    "card": "Card",
    "multiRowCard": "Multi-row card",
    "slicer": "Slicer",
    "advancedSlicerVisual": "Slicer",
    "listSlicer": "Slicer",
    "lineChart": "Line chart",
    "areaChart": "Area chart",
    "stackedAreaChart": "Stacked area chart",
    "hundredPercentStackedAreaChart": "100% stacked area chart",
    "columnChart": "Column chart",
    "clusteredColumnChart": "Clustered column chart",
    "stackedColumnChart": "Stacked column chart",
    "hundredPercentStackedColumnChart": "100% stacked column chart",
    "barChart": "Bar chart",
    "clusteredBarChart": "Clustered bar chart",
    "stackedBarChart": "Stacked bar chart",
    "hundredPercentStackedBarChart": "100% stacked bar chart",
    "lineClusteredColumnComboChart": "Line and clustered column chart",
    "lineStackedColumnComboChart": "Line and stacked column chart",
    "ribbonChart": "Ribbon chart",
    "waterfallChart": "Waterfall chart",
    "funnel": "Funnel",
    "scatterChart": "Scatter chart",
    "pieChart": "Pie chart",
    "donutChart": "Donut chart",
    "treemap": "Treemap",
    "map": "Map",
    "filledMap": "Filled map",
    "azureMap": "Azure map",
    "gauge": "Gauge",
    "kpi": "KPI",
    "decompositionTreeVisual": "Decomposition tree",
    "keyDriversVisual": "Key influencers",
    "textbox": "Text box",
    "image": "Image",
    "shape": "Shape",
    "actionButton": "Button",
}

_CHROME = (
    ("textbox", "text box", "text boxes"),
    ("image", "image", "images"),
    ("shape", "shape", "shapes"),
    ("actionButton", "button", "buttons"),
    ("visualGroup", "group", "groups"),
)

_FORMAT_LABELS = {
    "backColor": "Background color",
    "fontColor": "Font color",
    "backgroundColor": "Background color",
}

_COMPARE = {
    0: "equals",
    1: "is greater than",
    2: "is greater than or equal to",
    3: "is less than",
    4: "is less than or equal to",
}

_SELECT_KEYS = ("Column", "Measure", "Aggregation", "HierarchyLevel")


@dataclass(frozen=True)
class ParsedField:
    kind: str
    table: str
    name: str
    aggregation: str | None = None
    detail: str = ""


@dataclass
class FieldBinding:
    """One use of a column or measure on a visual, page, or the report."""

    how: str
    kind: str
    table: str
    name: str
    aggregation: str | None = None
    display_name: str | None = None
    detail: str = ""
    sort: str = ""
    inactive: bool = False

    def notes(self) -> str:
        parts: list[str] = []
        if self.detail:
            parts.append(self.detail)
        if self.inactive:
            parts.append("Inactive drill level")
        if self.sort:
            parts.append(self.sort)
        return ". ".join(parts)


@dataclass
class Visual:
    name: str
    visual_type: str
    type_label: str
    title: str | None
    title_hidden: bool
    heading: str
    filename: str
    bindings: list[FieldBinding]
    y: float
    x: float
    folder: str = ""
    page_name: str = ""
    page_display: str = ""
    page_hidden: bool = False

    @property
    def doc_rel(self) -> str:
        return f"Visuals/{self.folder}/{self.filename}"


@dataclass
class ReportPage:
    name: str
    display_name: str
    hidden: bool
    folder: str
    filters: list[FieldBinding]
    visuals: list[Visual]
    chrome: list[tuple[str, int]] = field(default_factory=list)

    def chrome_sentence(self) -> str:
        phrases = [
            _count_phrase(count, singular, plural)
            for _key, singular, plural, count in (
                (*item, self._chrome_count(item[0])) for item in _CHROME
            )
            if count
        ]
        known = {item[0] for item in _CHROME}
        for key, count in self.chrome:
            if key in known or count == 0:
                continue
            label = _TYPE_LABELS.get(key, key)
            phrases.append(_count_phrase(count, label, label + "s"))
        if not phrases:
            return ""
        if len(phrases) == 1:
            listed = phrases[0]
        else:
            listed = ", ".join(phrases[:-1]) + f", and {phrases[-1]}"
        return (
            f"This page also contains {listed}. "
            "Those visuals contain no tables, columns, or measures."
        )

    def _chrome_count(self, key: str) -> int:
        return next((count for name, count in self.chrome if name == key), 0)


@dataclass
class ReportDefinition:
    pages: list[ReportPage]
    filters: list[FieldBinding]

    @property
    def visuals(self) -> list[Visual]:
        return [visual for page in self.pages for visual in page.visuals]


def load_report(path: str | Path) -> ReportDefinition:
    """Load ``pages/pages.json``, each page, and each visual."""
    root = Path(path)
    pages_file = root / "pages" / "pages.json"
    if not pages_file.is_file():
        raise FileNotFoundError(f"report pages file not found: {pages_file}")
    meta = _read_json(pages_file)
    order = [name for name in meta.get("pageOrder") or [] if isinstance(name, str)]
    seen = set(order)
    extras = sorted(
        (
            child.name for child in (root / "pages").iterdir()
            if child.is_dir() and child.name not in seen and (child / "page.json").is_file()
        ),
        key=str.casefold,
    )
    pages = [_load_page(root, name) for name in order + extras]
    _assign_page_folders(pages)
    for page in pages:
        _assign_visual_files(page)
        for visual in page.visuals:
            visual.folder = page.folder
            visual.page_name = page.name
            visual.page_display = page.display_name
            visual.page_hidden = page.hidden
    report_filters = _filter_bindings(
        (_read_json(root / "report.json").get("filterConfig") or {}).get("filters") or [],
        "Report filter",
    )
    return ReportDefinition(pages, report_filters)


def role_label(visual_type: str, role: str) -> str:
    """Power BI field-well name for a query role on this visual type."""
    typed = _TYPE_ROLES.get(visual_type, {})
    if role in typed:
        return typed[role]
    if visual_type in _BAR_TYPES:
        if role == "Category":
            return "Y-axis"
        if role == "Y":
            return "X-axis"
    if role == "Rows" and visual_type in _SMALL_MULTIPLE_TYPES:
        return "Small multiples"
    return _GENERIC_ROLES.get(role, role)


def type_label(visual_type: str) -> str:
    return _TYPE_LABELS.get(visual_type, visual_type)


def _load_page(root: Path, name: str) -> ReportPage:
    path = root / "pages" / name / "page.json"
    if not path.is_file():
        raise FileNotFoundError(f"report page not found: {path}")
    payload = _read_json(path)
    visuals_dir = path.parent / "visuals"
    containers = []
    if visuals_dir.is_dir():
        for visual_json in sorted(visuals_dir.glob("*/visual.json"), key=lambda item: item.as_posix().casefold()):
            containers.append(_read_json(visual_json))
    data: list[Visual] = []
    chrome: dict[str, int] = {}
    for container in containers:
        if "visualGroup" in container and "visual" not in container:
            chrome["visualGroup"] = chrome.get("visualGroup", 0) + 1
            continue
        body = container.get("visual") or {}
        visual_type = body.get("visualType") or "visual"
        bindings = _visual_bindings(visual_type, body, container)
        if not bindings:
            chrome[visual_type] = chrome.get(visual_type, 0) + 1
            continue
        title, hidden = _title(body)
        position = container.get("position") or {}
        data.append(Visual(
            name=str(container.get("name") or ""),
            visual_type=visual_type,
            type_label=type_label(visual_type),
            title=title,
            title_hidden=hidden,
            heading="",
            filename="",
            bindings=bindings,
            y=float(position.get("y") or 0),
            x=float(position.get("x") or 0),
        ))
    data.sort(key=lambda item: (item.y, item.x, item.name))
    _assign_headings(data)
    filters = _filter_bindings(
        (payload.get("filterConfig") or {}).get("filters") or [],
        "Page filter",
    )
    return ReportPage(
        name=name,
        display_name=str(payload.get("displayName") or name),
        hidden=payload.get("visibility") == "HiddenInViewMode",
        folder="",
        filters=filters,
        visuals=data,
        chrome=[(key, chrome[key]) for key in chrome],
    )


def _visual_bindings(visual_type: str, body: dict, container: dict) -> list[FieldBinding]:
    bindings = _projection_bindings(visual_type, (body.get("query") or {}))
    _apply_sort(bindings, (body.get("query") or {}).get("sortDefinition") or {})
    bindings.extend(_formatting_bindings(body))
    bindings.extend(_container_filters(visual_type, body, container))
    return bindings


def _projection_bindings(visual_type: str, query: dict) -> list[FieldBinding]:
    state = query.get("queryState") or {}
    roles = [role for role in state if isinstance(state.get(role), dict)]

    def sort_key(role: str) -> tuple:
        try:
            position = _ROLE_ORDER.index(role)
        except ValueError:
            position = len(_ROLE_ORDER)
        return (position, roles.index(role))

    bindings: list[FieldBinding] = []
    for role in sorted(roles, key=sort_key):
        for projection in state[role].get("projections") or []:
            if not isinstance(projection, dict):
                continue
            parsed = _parse_select(projection.get("field") or {}, {})
            if parsed is None:
                continue
            aggregation = parsed.aggregation
            if aggregation is None and parsed.kind == "column":
                aggregation = _aggregation_prefix(projection.get("queryRef"))
            native = projection.get("nativeQueryRef") or ""
            if aggregation == "Count" and "distinct" in str(native).casefold():
                aggregation = "Distinct count"
            label = projection.get("displayName") or native
            bindings.append(FieldBinding(
                how=role_label(visual_type, role),
                kind=parsed.kind,
                table=parsed.table,
                name=parsed.name,
                aggregation=aggregation,
                display_name=_shown_as(parsed.name, label),
                detail=parsed.detail,
                inactive=projection.get("active") is False,
            ))
    return bindings


def _apply_sort(bindings: list[FieldBinding], sort_definition: dict) -> None:
    for item in sort_definition.get("sort") or []:
        if not isinstance(item, dict):
            continue
        parsed = _parse_select(item.get("field") or {}, {})
        if parsed is None:
            continue
        direction = "Sorted descending" if item.get("direction") == "Descending" else "Sorted ascending"
        if sort_definition.get("isDefaultSort"):
            direction += " by default"
        matched = False
        for binding in bindings:
            if not _same_field(binding, parsed):
                continue
            if binding.sort:
                continue
            binding.sort = direction
            matched = True
        if matched:
            continue
        bindings.append(FieldBinding(
            how="Sort",
            kind=parsed.kind,
            table=parsed.table,
            name=parsed.name,
            aggregation=parsed.aggregation,
            detail=parsed.detail,
            sort=direction,
        ))


def _same_field(binding: FieldBinding, parsed: ParsedField) -> bool:
    if binding.kind != parsed.kind:
        return False
    if binding.table.casefold() != parsed.table.casefold():
        return False
    if binding.name.casefold() != parsed.name.casefold():
        return False
    if (binding.aggregation or "") != (parsed.aggregation or ""):
        return False
    return (binding.detail or "") == (parsed.detail or "")


def _formatting_bindings(body: dict) -> list[FieldBinding]:
    found: list[FieldBinding] = []
    seen: set[tuple] = set()

    def walk(node: object, label: str, in_expr: bool) -> None:
        if isinstance(node, dict):
            if in_expr and any(key in node for key in _SELECT_KEYS):
                parsed = _parse_select(node, {})
                if parsed is not None and parsed.name:
                    key = (label, parsed.kind, parsed.table.casefold(), parsed.name.casefold())
                    if key not in seen:
                        seen.add(key)
                        found.append(FieldBinding(
                            how=label,
                            kind=parsed.kind,
                            table=parsed.table,
                            name=parsed.name,
                            aggregation=parsed.aggregation,
                            detail=parsed.detail,
                        ))
                return
            for key, value in node.items():
                if key in ("filter", "selfFilter", "selector"):
                    continue
                walk(value, _FORMAT_LABELS.get(key, label), in_expr or key == "expr")
        elif isinstance(node, list):
            for item in node:
                walk(item, label, in_expr)

    walk(body.get("objects") or {}, "Conditional formatting", False)
    walk(body.get("visualContainerObjects") or {}, "Conditional formatting", False)
    return found


def _container_filters(visual_type: str, body: dict, container: dict) -> list[FieldBinding]:
    items = []
    for host in (container, body):
        items.extend((host.get("filterConfig") or {}).get("filters") or [])
    bindings = _filter_bindings(items, "Filter")
    for prop, how in (
        ("filter", "Slicer selection" if visual_type == "slicer" else "Filter"),
        ("selfFilter", "Slicer search"),
    ):
        for general in (body.get("objects") or {}).get("general") or []:
            value = (general.get("properties") or {}).get(prop)
            if not isinstance(value, dict):
                continue
            embedded = value.get("filter")
            if isinstance(embedded, dict):
                binding = _binding_from_body(embedded, how)
                if binding is not None:
                    bindings.append(binding)
    # A filter-pane slot with no saved condition repeats a field that is already
    # in a well. Keep conditions that actually limit the visual.
    return [
        binding for binding in _dedupe_filters(bindings)
        if _condition_is_saved(binding.detail)
    ]


def _filter_bindings(items: list, how: str) -> list[FieldBinding]:
    bindings: list[FieldBinding] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        body = item.get("filter") if isinstance(item.get("filter"), dict) else None
        aliases = _aliases(body)
        parsed = _parse_select(item.get("field") or {}, aliases)
        if parsed is None and body is not None:
            parsed = _first_field(body, aliases)
        if parsed is None or not parsed.name:
            continue
        inverted, hidden = _filter_flags(item)
        bindings.append(FieldBinding(
            how=how,
            kind=parsed.kind,
            table=parsed.table,
            name=parsed.name,
            aggregation=parsed.aggregation,
            display_name=_shown_as(parsed.name, item.get("displayName")),
            detail=_filter_detail(body, inverted=inverted, hidden=hidden),
        ))
    return bindings


def _binding_from_body(body: dict, how: str) -> FieldBinding | None:
    parsed = _first_field(body, _aliases(body))
    if parsed is None or not parsed.name:
        return None
    return FieldBinding(
        how=how,
        kind=parsed.kind,
        table=parsed.table,
        name=parsed.name,
        aggregation=parsed.aggregation,
        detail=_filter_detail(body, inverted=False, hidden=False),
    )


def _dedupe_filters(bindings: list[FieldBinding]) -> list[FieldBinding]:
    seen: set[tuple] = set()
    result: list[FieldBinding] = []
    for binding in bindings:
        key = (
            binding.how,
            binding.kind,
            binding.table.casefold(),
            binding.name.casefold(),
            binding.detail.casefold(),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(binding)
    return result


def _parse_select(node: object, aliases: dict[str, str]) -> ParsedField | None:
    if not isinstance(node, dict):
        return None
    if "Aggregation" in node and isinstance(node["Aggregation"], dict):
        aggregation = node["Aggregation"]
        inner = _parse_select(aggregation.get("Expression") or {}, aliases)
        if inner is None:
            return None
        label = _aggregation_function(aggregation.get("Function"))
        return ParsedField(inner.kind, inner.table, inner.name, label or inner.aggregation, inner.detail)
    if "Column" in node and isinstance(node["Column"], dict):
        column = node["Column"]
        table = _entity((column.get("Expression") or {}).get("SourceRef"), aliases)
        name = str(column.get("Property") or "")
        if not name:
            return None
        return ParsedField("column", table, name)
    if "Measure" in node and isinstance(node["Measure"], dict):
        measure = node["Measure"]
        table = _entity((measure.get("Expression") or {}).get("SourceRef"), aliases)
        name = str(measure.get("Property") or "")
        if not name:
            return None
        return ParsedField("measure", table, name)
    if "HierarchyLevel" in node and isinstance(node["HierarchyLevel"], dict):
        level = node["HierarchyLevel"]
        hierarchy = (level.get("Expression") or {}).get("Hierarchy") or {}
        source = hierarchy.get("Expression") or {}
        if "PropertyVariationSource" in source:
            variation = source["PropertyVariationSource"] or {}
            table = _entity((variation.get("Expression") or {}).get("SourceRef"), aliases)
            name = str(variation.get("Property") or "")
        else:
            table = _entity(source.get("SourceRef"), aliases)
            name = ""
        hierarchy_name = str(hierarchy.get("Hierarchy") or "")
        level_name = str(level.get("Level") or "")
        if level_name and hierarchy_name:
            detail = f"{level_name} level of {hierarchy_name}"
        elif level_name:
            detail = f"{level_name} level"
        else:
            detail = hierarchy_name
        if not name and not table:
            return None
        return ParsedField("column", table, name, detail=detail)
    return None


def _first_field(node: object, aliases: dict[str, str]) -> ParsedField | None:
    if isinstance(node, dict):
        if any(key in node for key in _SELECT_KEYS):
            parsed = _parse_select(node, aliases)
            if parsed is not None and parsed.name:
                return parsed
        for key, value in node.items():
            if key in ("Annotations",):
                continue
            found = _first_field(value, aliases)
            if found is not None:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _first_field(item, aliases)
            if found is not None:
                return found
    return None


def _entity(source_ref: object, aliases: dict[str, str]) -> str:
    if not isinstance(source_ref, dict):
        return ""
    entity = source_ref.get("Entity")
    if entity:
        return str(entity)
    source = source_ref.get("Source")
    if source:
        return aliases.get(str(source), "")
    return ""


def _aliases(body: dict | None) -> dict[str, str]:
    aliases: dict[str, str] = {}
    if not isinstance(body, dict):
        return aliases
    for item in body.get("From") or []:
        if isinstance(item, dict) and item.get("Name") and item.get("Entity"):
            aliases[str(item["Name"])] = str(item["Entity"])
    return aliases


def _aggregation_function(value: object) -> str | None:
    if not isinstance(value, int):
        return None
    return _AGGREGATIONS.get(value, f"Function {value}")


def _aggregation_prefix(query_ref: object) -> str | None:
    if not isinstance(query_ref, str):
        return None
    for prefix, label in _QUERY_PREFIXES:
        if query_ref.startswith(prefix):
            return label
    return None


def _filter_detail(body: dict | None, *, inverted: bool, hidden: bool) -> str:
    summary = _summarize(body) if body else ""
    top = _topn_text(body) if body else ""
    if top and _summary_is_weak(summary):
        summary = top
    parts = [_sentence(summary) if summary else "No selection is saved"]
    if inverted:
        parts.append("Inverted selection")
    if hidden:
        parts.append("Hidden in view mode")
    return ". ".join(parts)


def _condition_is_saved(detail: str) -> bool:
    core = detail
    for phrase in ("Hidden in view mode", "Inverted selection", "No selection is saved"):
        core = core.replace(phrase, "")
    return bool(core.replace(".", "").strip())


def _summary_is_weak(summary: str) -> bool:
    return summary in ("", "includes a list of values", "excludes a list of values")


def _filter_flags(item: dict) -> tuple[bool, bool]:
    hidden = bool(item.get("isHiddenInViewMode"))
    inverted = False
    for general in (item.get("objects") or {}).get("general") or []:
        props = (general.get("properties") or {}) if isinstance(general, dict) else {}
        if _is_true(_property_literal(props.get("isInvertedSelectionMode"))):
            inverted = True
    return inverted, hidden


def _summarize(node: object, qualify: bool = False) -> str:
    if not isinstance(node, dict) or not node:
        return ""
    if "Where" in node:
        parts = [_summarize(item, qualify) for item in node.get("Where") or []]
        return "; ".join(part for part in parts if part)
    if "Condition" in node:
        return _summarize(node.get("Condition"), qualify)
    if "Not" in node:
        return _summarize_not(node.get("Not"), qualify)
    if "In" in node and isinstance(node["In"], dict):
        return _in_phrase(node["In"], "includes", qualify)
    if "Or" in node and isinstance(node["Or"], dict):
        return _join_logic(node["Or"], "or")
    if "And" in node and isinstance(node["And"], dict):
        return _join_logic(node["And"], "and")
    if "StartsWith" in node and isinstance(node["StartsWith"], dict):
        return _text_compare(node["StartsWith"], "starts with", qualify)
    if "Contains" in node and isinstance(node["Contains"], dict):
        return _text_compare(node["Contains"], "contains", qualify)
    if "Comparison" in node and isinstance(node["Comparison"], dict):
        return _comparison(node["Comparison"])
    return ""


def _summarize_not(node: object, qualify: bool = False) -> str:
    if not isinstance(node, dict):
        return ""
    target = node.get("Expression") if "Expression" in node else node
    if isinstance(target, dict) and "In" in target and isinstance(target["In"], dict):
        return _in_phrase(target["In"], "excludes", qualify)
    if isinstance(target, dict) and "Comparison" in target:
        text = _comparison(target["Comparison"])
        if text == "is blank":
            return "is not blank"
        return f"does not match ({text})" if text else ""
    text = _summarize(target, qualify)
    return f"not ({text})" if text else ""


def _join_logic(node: dict, word: str) -> str:
    parts = [
        part for part in (
            _summarize(node.get("Left"), True),
            _summarize(node.get("Right"), True),
        )
        if part
    ]
    return f" {word} ".join(parts)


def _in_phrase(node: dict, verb: str, qualify: bool) -> str:
    if "Table" in node and not _in_values(node):
        return ""
    names = _expression_names(node.get("Expressions") or [])
    values = _in_values(node)
    if len(names) > 1:
        return _join_values(f"{verb} {' and '.join(names)}", values)
    if qualify and len(names) == 1:
        return f"{names[0]} {_join_values(verb, values)}"
    return _join_values(verb, values)


def _text_compare(node: dict, verb: str, qualify: bool) -> str:
    target = _literal(node.get("Right")) or "a value"
    names = _expression_names([node.get("Left")])
    if (qualify or len(names) > 1) and names:
        return f"{names[0]} {verb} {target}"
    return f"{verb} {target}"


def _expression_names(expressions: list) -> list[str]:
    names: list[str] = []
    for expression in expressions:
        parsed = _parse_select(expression, {})
        if parsed is not None and parsed.name and parsed.name not in names:
            names.append(parsed.name)
    return names


def _topn_text(body: dict) -> str:
    query = _find_top_query(body)
    if not isinstance(query, dict):
        return ""
    top = query.get("Top")
    order = query.get("OrderBy") or []
    first = order[0] if order and isinstance(order[0], dict) else {}
    direction = "descending" if first.get("Direction") == 2 else "ascending"
    parsed = _parse_select(first.get("Expression") or {}, {})
    if parsed is None or not parsed.name:
        return f"Top {top}" if top else ""
    subject = f"{parsed.aggregation} of {parsed.name}" if parsed.aggregation else parsed.name
    amount = top if top is not None else "N"
    return f"Top {amount} by {subject}, {direction}"


def _find_top_query(node: object) -> dict | None:
    if isinstance(node, dict):
        if "Top" in node and "OrderBy" in node:
            return node
        for value in node.values():
            found = _find_top_query(value)
            if found is not None:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _find_top_query(item)
            if found is not None:
                return found
    return None


def _comparison(node: dict) -> str:
    kind = node.get("ComparisonKind", 0)
    right = _literal(node.get("Right"))
    if kind == 0 and right == "blank":
        return "is blank"
    op = _COMPARE.get(kind, "is compared with") if isinstance(kind, int) else "is compared with"
    return f"{op} {right}" if right else op


def _in_values(node: dict) -> list[str]:
    values: list[str] = []
    for row in node.get("Values") or []:
        if isinstance(row, list):
            bits = [_literal(item) for item in row]
            text = ", ".join(bit for bit in bits if bit)
        elif isinstance(row, dict):
            text = _literal(row)
        else:
            text = ""
        if text:
            values.append(text)
    return values


def _join_values(verb: str, values: list[str]) -> str:
    if not values:
        return f"{verb} a list of values"
    visible = values[:6]
    rest = len(values) - len(visible)
    if rest:
        return f"{verb} {', '.join(visible)}, and {rest} more"
    if len(visible) == 1:
        return f"{verb} {visible[0]}"
    if len(visible) == 2:
        return f"{verb} {visible[0]} and {visible[1]}"
    return f"{verb} {', '.join(visible[:-1])}, and {visible[-1]}"


def _literal(node: object) -> str:
    if not isinstance(node, dict):
        return ""
    literal = node.get("Literal")
    if isinstance(literal, dict):
        return _decode_literal(literal.get("Value"))
    return ""


def _decode_literal(value: object) -> str:
    if value is None:
        return ""
    text = str(value)
    if text == "null":
        return "blank"
    if len(text) >= 2 and text[0] == "'" and text[-1] == "'":
        return text[1:-1].replace("''", "'")
    return text


def _sentence(text: str) -> str:
    value = text.strip()
    if not value:
        return ""
    first = value.split(" ", 1)[0]
    if first.isalpha() and first[:1].islower():
        return value[0].upper() + value[1:]
    return value


def _property_literal(prop: object) -> object:
    if not isinstance(prop, dict):
        return None
    return ((prop.get("expr") or {}).get("Literal") or {}).get("Value")


def _is_true(value: object) -> bool:
    if value is None:
        return False
    return str(value).strip().strip("'").casefold() == "true"


def _title(body: dict) -> tuple[str | None, bool]:
    for item in (body.get("visualContainerObjects") or {}).get("title") or []:
        if not isinstance(item, dict):
            continue
        props = item.get("properties") or {}
        text = _decode_literal(_property_literal(props.get("text"))).strip()
        if not text:
            continue
        shown = _property_literal(props.get("show"))
        hidden = str(shown).strip().strip("'").casefold() == "false" if shown is not None else False
        return text, hidden
    return None, False


def _shown_as(name: str, label: object) -> str | None:
    if label is None:
        return None
    text = str(label).strip()
    if not text or text.casefold() == (name or "").casefold():
        return None
    return text


def _assign_headings(visuals: list[Visual]) -> None:
    bases = [_heading_for(visual) for visual in visuals]
    totals: dict[str, int] = {}
    for base in bases:
        totals[base] = totals.get(base, 0) + 1
    seen: dict[str, int] = {}
    for visual, base in zip(visuals, bases):
        if totals[base] == 1:
            visual.heading = base
            continue
        seen[base] = seen.get(base, 0) + 1
        visual.heading = base if seen[base] == 1 else f"{base} ({seen[base]})"


def _usable_label(label: str) -> bool:
    return any(character.isalnum() for character in label)


def _heading_for(visual: Visual) -> str:
    if visual.title:
        return f"{visual.type_label}: {visual.title}"
    names: list[str] = []
    for binding in visual.bindings:
        if binding.how in ("Filter", "Slicer selection", "Slicer search", "Sort", "Background color", "Font color", "Conditional formatting"):
            continue
        label = binding.display_name or binding.name
        if label and _usable_label(label) and label not in names:
            names.append(label)
        if len(names) == 3:
            break
    if not names:
        for binding in visual.bindings:
            label = binding.display_name or binding.name
            if label and _usable_label(label) and label not in names:
                names.append(label)
            if len(names) == 3:
                break
    if names:
        return f"{visual.type_label}: {', '.join(names)}"
    return visual.type_label or visual.name or "Visual"


def _assign_page_folders(pages: list[ReportPage]) -> None:
    used: set[str] = set()
    for page in pages:
        page.folder = _unique_stem(page.display_name or page.name, used)


def _assign_visual_files(page: ReportPage) -> None:
    used: set[str] = set()
    for visual in page.visuals:
        stem = _unique_stem(visual.heading or visual.name, used)
        visual.filename = stem + ".md"


def _unique_stem(label: str, used: set[str]) -> str:
    stem = _stem(label)
    candidate = stem
    number = 2
    while candidate.casefold() in used:
        candidate = f"{stem}-{number}"
        number += 1
    used.add(candidate.casefold())
    return candidate


def _stem(label: str) -> str:
    stem = safe_stem(label)
    stem = re.sub(r"_+", "_", stem).strip("._")
    return stem or "visual"


def _count_phrase(count: int, singular: str, plural: str) -> str:
    word = singular if count == 1 else plural
    return f"{count} {word}"


def _read_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ReportSyntaxError(f"{path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ReportSyntaxError(f"{path}: expected a JSON object")
    return payload
