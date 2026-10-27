"""Load a TMDL semantic-model folder into a :class:`SemanticModel` graph."""

from __future__ import annotations

from pathlib import Path

from . import dax, m_sources
from .model import (
    COLUMN_CALCULATED,
    COLUMN_CALCULATED_TABLE,
    COLUMN_DATA,
    TABLE_AUTO_DATE,
    TABLE_CALCULATED,
    TABLE_IMPORTED,
    TABLE_MIXED,
    TABLE_NO_PARTITION,
    Annotation,
    Column,
    Dependency,
    Diagnostic,
    Hierarchy,
    HierarchyLevel,
    Measure,
    ObjectRef,
    Partition,
    Relationship,
    SemanticModel,
    SharedExpression,
    Table,
    Variation,
)
from .naming import assign_filenames, decode_tmdl_filename
from .tmdl import Node, parse_tmdl, split_qualified

AUTO_DATE_PREFIXES = ("LocalDateTable_", "DateTableTemplate_")
_IMPORT_PARTITION_TYPES = {"m", "entity", "query", "policyrange"}
_SKIPPED_DIRECTORIES = {"cultures"}


def _annotations(node: Node) -> list[Annotation]:
    return [Annotation(a.name or "", a.value or "") for a in node.objects("annotation")]


def _description(node: Node) -> str | None:
    return node.description or node.prop("description")


def _plain_properties(node: Node) -> dict[str, str]:
    return {
        child.keyword: child.value or ""
        for child in node.children
        if child.kind == "property" and not child.is_expression
    }


def _expression_property(node: Node, key: str) -> str | None:
    for child in node.children:
        if child.kind == "property" and child.keyword == key and child.is_expression:
            return child.value
    return None


def _build_column(node: Node, table: str) -> Column:
    props = _plain_properties(node)
    expression = node.value if node.is_expression else None
    return Column(
        name=node.name or "",
        table=table,
        kind=COLUMN_CALCULATED if expression is not None else COLUMN_DATA,
        data_type=props.get("dataType"),
        source_column=props.get("sourceColumn"),
        expression=expression,
        is_hidden=node.has_flag("isHidden"),
        format_string=props.get("formatString"),
        description=_description(node),
        display_folder=props.get("displayFolder"),
        summarize_by=props.get("summarizeBy"),
        sort_by_column=props.get("sortByColumn"),
        data_category=props.get("dataCategory"),
        lineage_tag=props.get("lineageTag"),
        properties=props,
        flags=node.flags(),
        annotations=_annotations(node),
        variations=[
            Variation(
                name=v.name or "",
                relationship=v.prop("relationship"),
                default_hierarchy=v.prop("defaultHierarchy"),
                is_default=v.has_flag("isDefault"),
            )
            for v in node.objects("variation")
        ],
    )


def _build_measure(node: Node, table: str) -> Measure:
    props = _plain_properties(node)
    format_definition = node.first_object("formatStringDefinition")
    format_expression = _expression_property(node, "formatStringDefinition")
    if format_expression is None and format_definition is not None:
        format_expression = format_definition.value
    return Measure(
        name=node.name or "",
        table=table,
        expression=node.value or "",
        format_string=props.get("formatString"),
        format_string_expression=format_expression,
        description=_description(node),
        display_folder=props.get("displayFolder"),
        is_hidden=node.has_flag("isHidden"),
        lineage_tag=props.get("lineageTag"),
        properties=props,
        flags=node.flags(),
        annotations=_annotations(node),
    )


def _build_partition(node: Node, table: str) -> Partition:
    props = _plain_properties(node)
    expression = _expression_property(node, "source")
    for child in node.children:
        if child.keyword == "source" and not child.is_expression:
            for nested in child.children:
                if nested.kind == "property":
                    props[f"source.{nested.keyword}"] = nested.value or ""
    return Partition(
        name=node.name or "",
        table=table,
        source_type=(node.value or "").strip(),
        expression=expression,
        mode=props.get("mode"),
        query_group=props.get("queryGroup"),
        description=_description(node),
        properties=props,
        annotations=_annotations(node),
    )


def _classify(table: Table) -> str:
    annotations = {a.name: a.value.strip().lower() for a in table.annotations}
    if (
        table.name.startswith(AUTO_DATE_PREFIXES)
        or annotations.get("__PBI_LocalDateTable") == "true"
        or annotations.get("__PBI_TemplateDateTable") == "true"
    ):
        return TABLE_AUTO_DATE
    categories = set()
    for partition in table.partitions:
        kind = partition.source_type.lower()
        if kind in _IMPORT_PARTITION_TYPES:
            categories.add(TABLE_IMPORTED)
        elif kind == "calculated":
            categories.add(TABLE_CALCULATED)
        else:
            categories.add(kind or "unknown")
    if not categories:
        return TABLE_NO_PARTITION
    if len(categories) > 1:
        return TABLE_MIXED
    return categories.pop()


def _build_table(node: Node, file_path: str) -> Table:
    name = node.name or ""
    props = _plain_properties(node)
    table = Table(
        name=name,
        file_path=file_path,
        description=_description(node),
        is_hidden=node.has_flag("isHidden"),
        is_private=node.has_flag("isPrivate"),
        lineage_tag=props.get("lineageTag"),
        properties=props,
        flags=node.flags(),
        annotations=_annotations(node),
        columns=[_build_column(c, name) for c in node.objects("column")],
        measures=[_build_measure(m, name) for m in node.objects("measure")],
        partitions=[_build_partition(p, name) for p in node.objects("partition")],
        hierarchies=[
            Hierarchy(
                name=h.name or "",
                table=name,
                levels=[HierarchyLevel(lv.name or "", lv.prop("column")) for lv in h.objects("level")],
                is_hidden=h.has_flag("isHidden"),
                description=_description(h),
                annotations=_annotations(h),
            )
            for h in node.objects("hierarchy")
        ],
    )
    table.classification = _classify(table)
    if table.classification == TABLE_CALCULATED:
        for column in table.columns:
            if column.kind == COLUMN_DATA:
                column.kind = COLUMN_CALCULATED_TABLE
    return table


def _build_relationship(node: Node, location: str, diagnostics: list[Diagnostic]) -> Relationship:
    props = {flag: "true" for flag in node.flags()}
    props.update(_plain_properties(node))
    endpoints = []
    for key in ("fromColumn", "toColumn"):
        parts = split_qualified(props.get(key, "")) if props.get(key) else []
        if len(parts) != 2:
            diagnostics.append(Diagnostic(
                "warning", f"relationship {node.name}: cannot parse {key} {props.get(key)!r}", location))
            parts = (parts + ["", ""])[:2]
        endpoints.extend(parts)
    return Relationship(
        name=node.name or "",
        from_table=endpoints[0],
        from_column=endpoints[1],
        to_table=endpoints[2],
        to_column=endpoints[3],
        properties=props,
        annotations=_annotations(node),
    )


def _tmdl_files(model_dir: Path) -> list[Path]:
    files = [
        p for p in model_dir.rglob("*.tmdl")
        if not (set(p.relative_to(model_dir).parts[:-1]) & _SKIPPED_DIRECTORIES)
    ]
    return sorted(files, key=lambda p: p.relative_to(model_dir).as_posix().casefold())


def load_model(model_dir: str | Path) -> SemanticModel:
    """Parse every TMDL file under ``model_dir`` and build the model graph."""
    root = Path(model_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"semantic model definition folder not found: {root}")
    model = SemanticModel(name=root.parent.name.removesuffix(".SemanticModel") or root.name,
                          model_dir=str(root))
    culture_dir = root / "cultures"
    if culture_dir.is_dir():
        model.cultures = sorted(decode_tmdl_filename(p.stem) for p in culture_dir.glob("*.tmdl"))

    for path in _tmdl_files(root):
        rel = path.relative_to(root).as_posix()
        nodes = parse_tmdl(path.read_text(encoding="utf-8-sig"), rel)
        file_has_model = any(n.keyword == "model" for n in nodes)
        for node in nodes:
            _dispatch(model, node, rel, path, file_has_model)

    model.tables.sort(key=lambda t: t.name.casefold())
    filenames = assign_filenames([t.name for t in model.tables])
    for table in model.tables:
        table.doc_filename = filenames[table.name]

    _validate(model)
    _analyze(model)
    return model


def _dispatch(model: SemanticModel, node: Node, rel: str, path: Path, file_has_model: bool) -> None:
    location = f"{rel}:{node.line}"
    if node.keyword == "model" and not node.is_ref:
        model.properties.update(_plain_properties(node))
        model.flags.extend(node.flags())
        model.annotations.extend(_annotations(node))
        for child in node.objects("table"):
            if child.is_ref:
                model.referenced_tables.append(child.name or "")
    elif node.keyword == "database":
        model.database_properties.update(_plain_properties(node))
    elif node.keyword == "table" and node.is_ref:
        model.referenced_tables.append(node.name or "")
    elif node.keyword == "table":
        table = _build_table(node, rel)
        decoded = decode_tmdl_filename(path.stem)
        if rel.startswith("tables/") and decoded != table.name:
            model.diagnostics.append(Diagnostic(
                "info", f"file name decodes to {decoded!r} but declares table {table.name!r}", location))
        model.tables.append(table)
    elif node.keyword == "relationship":
        model.relationships.append(_build_relationship(node, location, model.diagnostics))
    elif node.keyword == "expression":
        model.expressions.append(SharedExpression(
            name=node.name or "",
            expression=node.value or "",
            kind=node.prop("kind"),
            description=_description(node),
            annotations=_annotations(node),
        ))
    elif node.keyword == "annotation" and file_has_model:
        model.annotations.append(Annotation(node.name or "", node.value or ""))
    elif node.keyword == "cultureInfo" and node.is_ref:
        if node.name and node.name not in model.cultures:
            model.cultures.append(node.name)
    else:
        model.diagnostics.append(Diagnostic(
            "info", f"top-level {node.keyword} {node.name or ''} not documented".rstrip(), location))


def _validate(model: SemanticModel) -> None:
    tables = {t.name.casefold(): t for t in model.tables}
    for name in model.referenced_tables:
        if name.casefold() not in tables:
            model.diagnostics.append(Diagnostic("warning", f"model references missing table {name!r}"))
    referenced = {n.casefold() for n in model.referenced_tables}
    if referenced:
        for table in model.tables:
            if table.name.casefold() not in referenced:
                model.diagnostics.append(Diagnostic(
                    "info", f"table {table.name!r} is not listed in model.tmdl", table.file_path))

    relationship_ids = {r.name for r in model.relationships}
    for rel in model.relationships:
        for side, table_name, column_name in (
            ("from", rel.from_table, rel.from_column),
            ("to", rel.to_table, rel.to_column),
        ):
            table = tables.get(table_name.casefold())
            if table is None:
                rel.valid = False
                model.diagnostics.append(Diagnostic(
                    "warning", f"relationship {rel.name}: {side} table {table_name!r} not found"))
                continue
            column = table.column(column_name)
            if column is None:
                rel.valid = False
                model.diagnostics.append(Diagnostic(
                    "warning", f"relationship {rel.name}: {side} column {table_name}[{column_name}] not found"))
            elif side == "from":
                rel.from_table, rel.from_column = table.name, column.name
            else:
                rel.to_table, rel.to_column = table.name, column.name

    for table in model.tables:
        for column in table.columns:
            for variation in column.variations:
                if variation.relationship and variation.relationship not in relationship_ids:
                    model.diagnostics.append(Diagnostic(
                        "warning",
                        f"column {table.name}[{column.name}] variation references unknown "
                        f"relationship {variation.relationship}",
                        table.file_path,
                    ))
    model.relationships.sort(key=lambda r: (r.from_table.casefold(), r.from_column.casefold(),
                                            r.to_table.casefold(), r.to_column.casefold(), r.name))


def _analyze(model: SemanticModel) -> None:
    index = dax.ModelIndex(model.tables)
    edges: set[Dependency] = set()

    def add_dax_edges(source: ObjectRef, refs) -> None:
        for ref in refs:
            target = ref.target
            if target is not None and target != source:
                edges.add(Dependency(source, target, "dax"))

    query_names = {t.name for t in model.tables} | {e.name for e in model.expressions}
    expression_names = {e.name for e in model.expressions}

    def add_query_edges(source: ObjectRef, names: list[str]) -> None:
        for name in names:
            if name in expression_names:
                target = ObjectRef("expression", "", name)
            else:
                target = ObjectRef("table", name)
            if target != ObjectRef("table", source.table):
                edges.add(Dependency(source, target, "m"))

    for table in model.tables:
        for measure in table.measures:
            measure.references = dax.analyze_dax(measure.expression, table.name, dax.CONTEXT_MEASURE, index)
            if measure.format_string_expression:
                measure.references = dax.dedupe_references(measure.references + dax.analyze_dax(
                    measure.format_string_expression, table.name, dax.CONTEXT_MEASURE, index))
            add_dax_edges(measure.ref, measure.references)
        for column in table.columns:
            if column.expression is not None:
                column.references = dax.analyze_dax(column.expression, table.name, dax.CONTEXT_COLUMN, index)
                add_dax_edges(column.ref, column.references)
        for partition in table.partitions:
            if not partition.expression:
                continue
            if partition.language == "dax":
                partition.references = dax.analyze_dax(
                    partition.expression, table.name, dax.CONTEXT_TABLE, index)
                add_dax_edges(partition.ref, partition.references)
            elif partition.language == "m":
                partition.query_references = m_sources.query_references(
                    partition.expression, query_names - {table.name})
                add_query_edges(partition.ref, partition.query_references)
                model.sources.extend(m_sources.extract_sources(
                    partition.expression, table.name, partition.name))

    for expression in model.expressions:
        if expression.kind not in (None, "m"):
            continue
        expression.query_references = m_sources.query_references(
            expression.expression, query_names - {expression.name})
        add_query_edges(ObjectRef("expression", "", expression.name), expression.query_references)
        model.sources.extend(m_sources.extract_sources(expression.expression, "", expression.name))

    model.dependencies = sorted(edges)
    model.sources.sort(key=lambda s: (s.table.casefold(), s.partition.casefold(), s.step, s.connector))
