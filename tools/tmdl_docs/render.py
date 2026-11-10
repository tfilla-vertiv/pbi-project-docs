"""Render a parsed semantic model, and the report visuals that use it, as linked Markdown.

Authored tables get a page under ``Tables/``. ``LocalDateTable_*`` and
``DateTableTemplate_*`` tables are omitted; they are Power BI's hidden date tables.
Visuals that bind model fields get a page under ``Visuals/``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import quote, unquote

from .markdown import (
    MARKER,
    Raw,
    align_link_fragments,
    retarget_heading_links,
    bullets,
    code_span,
    document,
    fenced_code,
    heading,
    html_code,
    markdown_table,
    md_link,
    numbered,
    slug,
)
from .model import (
    COLUMN_CALCULATED,
    COLUMN_CALCULATED_TABLE,
    COLUMN_DATA,
    REF_AMBIGUOUS,
    REF_LOCAL,
    REF_RESOLVED,
    REF_UNRESOLVED,
    TABLE_AUTO_DATE,
    TABLE_CALCULATED,
    TABLE_IMPORTED,
    TABLE_MIXED,
    TABLE_NO_PARTITION,
    Annotation,
    Column,
    DataSource,
    DaxReference,
    Measure,
    ObjectRef,
    Partition,
    Relationship,
    SemanticModel,
    Table,
)
from .report import FieldBinding, ReportDefinition, ReportPage, Visual

_CLASS_LABELS = {
    TABLE_IMPORTED: "Imported",
    TABLE_CALCULATED: "Calculated",
    TABLE_AUTO_DATE: "Auto-date",
    TABLE_MIXED: "Mixed",
    TABLE_NO_PARTITION: "No partition",
}
_COLUMN_KINDS = {
    COLUMN_DATA: "Data",
    COLUMN_CALCULATED: "Calculated",
    COLUMN_CALCULATED_TABLE: "Calculated table",
}
_CLASS_ORDER = (TABLE_IMPORTED, TABLE_CALCULATED, TABLE_MIXED, TABLE_NO_PARTITION, TABLE_AUTO_DATE)
_CROSS_FILTER = {
    "oneDirection": "Single direction",
    "bothDirections": "Both directions",
    "automatic": "Automatic",
}
_DATE_JOIN = {
    "datePartOnly": "Date part only",
    "dateAndTime": "Date and time",
}
_DETAIL_RANK = (
    "entity", "workspaceId", "dataflowId", "Id", "server", "database",
    "url", "site", "path", "connection", "query",
)
_ROUTINE_NOTES = frozenset({
    "unqualified column resolved against owning table",
    "unqualified column in row context of owning table",
})
_SKIP_COLUMN_ANNOTATIONS = frozenset({("SummarizationSetBy", "Automatic")})
_BAD_STATUSES = frozenset({REF_AMBIGUOUS, REF_UNRESOLVED})


@dataclass(frozen=True)
class WriteResult:
    """Files written and stale generated table pages removed."""

    written: tuple[str, ...]
    removed: tuple[str, ...]


def render_documentation(
    model: SemanticModel,
    *,
    model_dir_display: str,
    model_href: str,
    regenerate_command: str,
    using_defaults: bool = False,
    report: ReportDefinition | None = None,
    report_dir_display: str = "",
    report_href: str = "",
    report_description: str = "",
    published: date | None = None,
) -> dict[str, str]:
    """Return ``relative path -> Markdown`` for every generated file."""
    renderer = Renderer(
        model,
        model_dir_display=model_dir_display,
        model_href=model_href,
        regenerate_command=regenerate_command,
        using_defaults=using_defaults,
        report=report,
        report_dir_display=report_dir_display,
        report_href=report_href,
        report_description=report_description,
        published=published,
    )
    return renderer.render()


def write_documentation(output_dir: str | Path, files: dict[str, str]) -> WriteResult:
    """Write generated files and delete stale generator-owned pages.

    A file under ``Tables/`` or ``Visuals/`` is removed only when it is not part
    of this run and its first line is the generator marker. ``README.md``,
    ``Model.md``, ``Measures.md``, ``Sources.md``, and ``Visuals.md`` are
    rewritten in place. Anything else in the output folder is left alone.
    """
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    expected = set(files)
    removed: list[str] = []
    _remove_stale(root, "Tables", expected, removed)
    _remove_stale(root, "Visuals", expected, removed)
    written: list[str] = []
    for rel, content in files.items():
        target = _output_path(root, rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        text = _cursor_file_links(root, rel, content)
        text = text if text.endswith("\n") else text + "\n"
        target.write_text(text, encoding="utf-8", newline="\n")
        written.append(rel)
    return WriteResult(tuple(written), tuple(removed))


class Renderer:
    def __init__(
        self,
        model: SemanticModel,
        *,
        model_dir_display: str,
        model_href: str,
        regenerate_command: str,
        using_defaults: bool,
        report: ReportDefinition | None = None,
        report_dir_display: str = "",
        report_href: str = "",
        report_description: str = "",
        published: date | None = None,
    ):
        self.model = model
        self.model_dir_display = model_dir_display
        self.model_href = model_href
        self.regenerate_command = regenerate_command
        self.using_defaults = using_defaults
        self.report = report
        self.report_dir_display = report_dir_display
        self.report_href = report_href
        self.report_description = report_description
        self.published = date.today() if published is None else published
        self.source_groups = list(model.unique_sources())
        self.source_titles = _disambiguate_titles(self.source_groups)
        self.anchors: dict[tuple, str] = {}
        self.rel_keys: dict[int, tuple] = {}
        self._used_anchors: set[str] = set()
        self._use_index: dict[tuple[str, str, str], list[tuple]] | None = None
        self._build_anchors()

    def render(self) -> dict[str, str]:
        files = {
            "README.md": self._readme(),
            "Model.md": self._model_page(),
            "Measures.md": self._measures_page(),
            "Sources.md": self._sources_page(),
        }
        for table in sorted(self.model.authored_tables, key=lambda item: item.doc_filename.casefold()):
            files[f"Tables/{table.doc_filename}"] = self._table_page(table)
        if self.report is not None:
            files["Visuals.md"] = self._visuals_index()
            for report_page in self.report.pages:
                for visual in report_page.visuals:
                    files[visual.doc_rel] = self._visual_page(report_page, visual)
        return retarget_heading_links(align_link_fragments(files))

    def page(self, location: str, current_file: str) -> Page:
        return Page(self, location, current_file)

    def claim(self, key: tuple, *parts: str) -> None:
        base = slug(*parts)
        candidate = base
        number = 2
        while candidate in self._used_anchors:
            candidate = f"{base}-{number}"
            number += 1
        self._used_anchors.add(candidate)
        self.anchors[key] = candidate

    def _build_anchors(self) -> None:
        for table in self.model.tables:
            table_key = table.name.casefold()
            self.claim(("table", table_key), "table", table.name)
            for column in table.columns:
                self.claim(("column", table_key, column.name.casefold()), "column", table.name, column.name)
            for measure in table.measures:
                self.claim(
                    ("measure", table_key, measure.name.casefold()),
                    "measure", table.name, measure.name,
                )
            for partition in table.partitions:
                self.claim(
                    ("partition", table_key, partition.name.casefold()),
                    "partition", table.name, partition.name,
                )
                if partition.language == "m":
                    self.claim(
                        ("m-block", table_key, partition.name.casefold()),
                        "query", table.name, partition.name,
                    )
        counts: dict[str, int] = {}
        for rel in self.model.relationships:
            counts[rel.name] = counts.get(rel.name, 0) + 1
            number = counts[rel.name]
            key = ("relationship", rel.name, number)
            self.rel_keys[id(rel)] = key
            parts = ["relationship", rel.name]
            if number > 1:
                parts.append(str(number))
            self.claim(key, *parts)
        for (source, _records), title in zip(self.source_groups, self.source_titles):
            self.claim(("source", source.identity), "source", title)
        for expression in self.model.expressions:
            self.claim(("expression", expression.name.casefold()), "expression", expression.name)
        if self.report is None:
            return
        for report_page in self.report.pages:
            self.claim(("report-page", report_page.name), "page", report_page.display_name)
            for visual in report_page.visuals:
                self.claim(
                    ("visual", report_page.name, visual.name),
                    "visual",
                    report_page.display_name,
                    visual.heading,
                )

    def model_location(self) -> str:
        href = self.model_href
        if href and not _is_absolute_href(href):
            return md_link(self.model_dir_display, href)
        return code_span(self.model_dir_display)

    def report_location(self) -> str:
        href = self.report_href
        if href and not _is_absolute_href(href):
            return md_link(self.report_dir_display, href)
        return code_span(self.report_dir_display)

    def _readme(self) -> str:
        page = self.page("root", "README.md")
        authored = self.model.authored_tables
        auto = self.model.auto_date_tables
        columns = sum(len(table.columns) for table in self.model.tables)
        authored_columns = sum(len(table.columns) for table in authored)
        measures = self.model.measures
        relationships = self._catalog_relationships()
        inactive = sum(not rel.is_active for rel in relationships)
        bidirectional = sum(rel.is_bidirectional for rel in relationships)
        partitions = [partition for table in self.model.tables for partition in table.partitions]
        m_partitions = [partition for partition in partitions if partition.language == "m"]
        counts: dict[str, int] = {}
        for table in authored:
            counts[table.classification] = counts.get(table.classification, 0) + 1
        breakdown = ", ".join(
            f"{counts[kind]} {class_label(kind).lower()}"
            for kind in _CLASS_ORDER
            if counts.get(kind)
        )
        regenerate = [
            "From the Power BI project folder:",
            fenced_code(self.regenerate_command),
        ]
        if self.using_defaults:
            regenerate.append(
                "Run that command again after the model or report changes. "
                "It rewrites the Markdown in `docs/` and the HTML in `docs-html/`."
            )
        if self.report is not None:
            regenerate.append(
                "The generator rewrites `README.md`, `Model.md`, `Measures.md`, `Sources.md`, "
                "`Visuals.md`, the pages in `Tables/`, and the pages in `Visuals/`. On a later run it "
                "deletes a page in `Tables/` or `Visuals/` only when that file was produced by this "
                "generator and the table or visual is no longer present. Other files in the output "
                "folder are left in place."
            )
        else:
            regenerate.append(
                "The generator rewrites `README.md`, `Model.md`, `Measures.md`, `Sources.md`, and the "
                "pages in `Tables/`. On a later run it deletes a page in `Tables/` only when that file "
                "was produced by this generator and the table is no longer in the model. Other files in "
                "the output folder are left in place."
            )
        inventory = [
            ["Tables", len(self.model.tables)],
            [Raw(md_link("Authored tables", "#authored-tables")), len(authored)],
            ["Auto-date tables", len(auto)],
            ["Columns", columns],
            ["Columns on authored tables", authored_columns],
            [Raw(md_link("Measures", page.root("Measures.md"))), len(measures)],
            [Raw(md_link("Relationships", page.anchored(page.root("Model.md"), _STATIC["relationships"]))), len(relationships)],
            ["Inactive relationships", inactive],
            ["Bidirectional relationships", bidirectional],
            ["Partitions", len(partitions)],
            [Raw(md_link("Power Query partitions", page.anchored(page.root("Sources.md"), _STATIC["power-query"]))), len(m_partitions)],
            ["Physical source uses", len(self.model.sources)],
            [Raw(md_link("Distinct physical sources", page.root("Sources.md"))), len(self.source_groups)],
            [
                Raw(md_link("Diagnostics", page.anchored(page.root("Model.md"), _STATIC["diagnostics"])))
                if self.model.diagnostics else "Diagnostics",
                len(self.model.diagnostics),
            ],
        ]
        documents = [
            f"- {md_link('Model', page.root('Model.md'))}: imported and calculated tables, and the relationships that connect them.",
            f"- {md_link('Measures', page.root('Measures.md'))}: every measure, with its expression, dependencies, and reference diagnostics.",
            f"- {md_link('Sources', page.root('Sources.md'))}: physical sources and the Power Query expressions that read them.",
        ]
        if self.report is not None:
            visible_pages = sum(not item.hidden for item in self.report.pages)
            documents.append(
                f"- {md_link('Visuals', page.root('Visuals.md'))}: each visual that binds model fields, "
                "with the tables, columns, and measures it uses and the role of each one."
            )
            inventory.extend([
                [Raw(md_link("Report pages", page.root("Visuals.md"))), len(self.report.pages)],
                ["Visible report pages", visible_pages],
                [Raw(md_link("Visuals", page.root("Visuals.md"))), len(self.report.visuals)],
            ])
        documents.append("- [Authored tables](#authored-tables): one page for each table authored in the model.")
        index_rows = [
            [
                Raw(page.obj(table.ref)),
                class_label(table.classification),
                len(table.columns),
                len(table.measures),
                len(table.partitions),
                visibility(table),
            ]
            for table in authored
        ]
        return document([
            page.nav(),
            heading(1, self.model.name),
            published_line(self.published),
            report_description_section(self.report_description),
            f"Semantic model documentation generated from {self.model_location()}.",
            heading(2, "Regenerate") + "\n\n" + "\n\n".join(regenerate),
            heading(2, "Documents") + "\n\n" + "\n".join(documents),
            heading(2, "Inventory") + "\n\n" + markdown_table(["Item", "Count"], inventory),
            heading(2, "Authored tables", "authored-tables") + "\n\n"
            + f"{plural(len(authored), 'authored table')}: {breakdown}. "
            + (
                f"{plural(len(auto), 'auto-generated date table')} "
                + ("is" if len(auto) == 1 else "are")
                + " omitted from these pages.\n\n"
                if auto else "\n\n"
            )
            + markdown_table(
                ["Table", "Classification", "Columns", "Measures", "Partitions", "Status"],
                index_rows,
            ),
        ])

    def _model_page(self) -> str:
        page = self.page("root", "Model.md")
        return document([
            page.nav(),
            heading(1, "Model"),
            self._construction_intro(),
            self._authored_inventory(page),
            self._relationship_catalog(page),
            self._diagnostics(page) if self.model.diagnostics else None,
        ])

    def _construction_intro(self) -> str:
        counts: dict[str, int] = {}
        for table in self.model.authored_tables:
            counts[table.classification] = counts.get(table.classification, 0) + 1
        parts = [
            plural(counts[kind], f"{class_label(kind).lower()} table")
            for kind in _CLASS_ORDER
            if counts.get(kind) and kind != TABLE_AUTO_DATE
        ]
        for kind, count in counts.items():
            if kind not in _CLASS_ORDER and kind != TABLE_AUTO_DATE:
                parts.append(plural(count, f"{class_label(kind).lower()} table"))
        if not parts:
            body = f"{self.model.name} has no authored tables."
        elif len(parts) == 1:
            body = f"{self.model.name} is built from {parts[0]}."
        else:
            body = f"{self.model.name} is built from {', '.join(parts[:-1])} and {parts[-1]}."
        if _time_intelligence_enabled(self.model):
            body += (
                " Time intelligence is on, so each date column has a hidden date table. "
                "Those tables are left off this page."
            )
        return body

    def _authored_inventory(self, page: Page) -> str:
        grouped: dict[str, list[Table]] = {}
        for table in self.model.authored_tables:
            grouped.setdefault(table.classification, []).append(table)
        order = [key for key in _CLASS_ORDER if key in grouped]
        order.extend(key for key in grouped if key not in order)
        blocks = [
            heading(2, "Authored tables", "authored-tables")
            + "\n\nEach table links to its own page."
        ]
        if not order:
            blocks.append("None.")
        for classification in order:
            blocks.append(self._authored_group(page, classification, grouped[classification]))
        return "\n\n".join(blocks)

    def _authored_group(self, page: Page, classification: str, tables: list[Table]) -> str:
        label = class_label(classification)
        listed = tables
        refresh_note = ""
        if classification == TABLE_IMPORTED:
            listed, in_refresh_order = _refresh_order(tables, self.model.query_order())
            if in_refresh_order:
                refresh_note = "Listed in refresh order.\n\n"
        descriptions = any(table.description for table in listed)
        show_status = any(visibility(table) != "Visible" for table in listed)
        rows: list[list[object]] = []
        for table in listed:
            row: list[object] = [
                Raw(page.obj(table.ref)),
                len(table.columns),
                len(table.measures),
            ]
            if show_status:
                row.append(visibility(table))
            if descriptions:
                row.append(table.description or "")
            rows.append(row)
        headers = ["Table", "Columns", "Measures"]
        if show_status:
            headers.append("Status")
        if descriptions:
            headers.append("Description")
        return (
            heading(3, f"{label} tables", slug(label, "tables"))
            + "\n\n"
            + refresh_note
            + markdown_table(headers, rows)
        )

    def _in_relationship_catalog(self, rel: Relationship) -> bool:
        left = self.model.table(rel.from_table)
        right = self.model.table(rel.to_table)
        if left is None or right is None:
            return True
        return not left.is_auto_date and not right.is_auto_date

    def _catalog_relationships(self) -> list[Relationship]:
        return [rel for rel in self.model.relationships if self._in_relationship_catalog(rel)]

    def _relationship_catalog(self, page: Page) -> str:
        relationships = self._catalog_relationships()
        if not relationships:
            body = "No relationships connect the authored tables."
        else:
            inactive = sum(not rel.is_active for rel in relationships)
            bidirectional = sum(rel.is_bidirectional for rel in relationships)
            sentences = [f"{plural(len(relationships), 'relationship')} connect the authored tables."]
            if inactive:
                sentences.append("One is inactive." if inactive == 1 else f"{inactive} are inactive.")
            if bidirectional:
                sentences.append(
                    "One filters in both directions." if bidirectional == 1
                    else f"{bidirectional} of them filter in both directions."
                )
            body = " ".join(sentences) + "\n\n" + self._relationship_table(
                page, relationships, emit_anchors=True, show_names=False,
            )
        return heading(2, "Relationships", "relationships") + "\n\n" + body

    def _diagnostics(self, page: Page) -> str:
        del page
        rows = [
            [diagnostic.severity, diagnostic.location or "", diagnostic.message]
            for diagnostic in sorted(
                self.model.diagnostics,
                key=lambda item: (item.severity != "warning", item.location or "", item.message),
            )
        ]
        body = markdown_table(["Severity", "Location", "Message"], rows)
        return heading(2, "Diagnostics", "diagnostics") + "\n\n" + body

    def _measures_page(self) -> str:
        page = self.page("root", "Measures.md")
        hosts = [table for table in self.model.authored_tables if table.measures]
        auto_hosts = [table for table in self.model.auto_date_tables if table.measures]
        empty = [table for table in self.model.authored_tables if not table.measures]
        intro = f"{plural(len(self.model.measures), 'measure')} across {plural(len(hosts) + len(auto_hosts), 'table')}."
        if empty:
            intro += " Authored tables with no measures: " + ", ".join(page.obj(table.ref) for table in empty) + "."
        groups = hosts + auto_hosts
        blocks = [
            page.nav(),
            heading(1, "Measures"),
            intro,
            self._measures_toc(page, groups) if groups else None,
            self._reference_diagnostics(page),
        ]
        if not groups:
            blocks.append("This model has no measures.")
        for table in groups:
            blocks.append(self._measure_group(page, table))
        return document(blocks)

    def _measures_toc(self, page: Page, tables: list[Table]) -> str:
        lines: list[str] = []
        for table in tables:
            table_key = ("table", table.name.casefold())
            lines.append("- " + md_link(table.name, "Measures.md#" + self.anchors[table_key]))
            for measure in table.measures:
                key = ("measure", measure.table.casefold(), measure.name.casefold())
                lines.append("  - " + md_link(measure.name, "Measures.md#" + self.anchors[key]))
        return heading(2, "Contents") + "\n\n" + "\n".join(lines)

    def _reference_diagnostics(self, page: Page) -> str:
        rows = []
        local_count = 0
        for source, refs in _expression_references(self.model):
            for ref in refs:
                if ref.status == REF_LOCAL:
                    local_count += 1
                elif ref.status in _BAD_STATUSES:
                    rows.append([
                        Raw(page.obj(source)),
                        Raw(html_code(ref.raw)),
                        "Ambiguous" if ref.status == REF_AMBIGUOUS else "Unresolved",
                        _ref_detail(page, ref),
                    ])
        rows.sort(key=lambda row: (str(row[2]), str(row[0]), str(row[1])))
        if rows:
            body = markdown_table(["Object", "Reference", "Status", "Detail"], rows)
        else:
            body = (
                "Every DAX reference in measures, calculated columns, and calculated partitions "
                "resolved to a model object."
            )
        if local_count:
            body += (
                f"\n\n{plural(local_count, 'reference')} "
                + ("names a column" if local_count == 1 else "name columns")
                + " created inside an expression and "
                + ("is" if local_count == 1 else "are")
                + " not model dependencies."
            )
        return heading(2, "Reference diagnostics", "reference-diagnostics") + "\n\n" + body

    def _measure_group(self, page: Page, table: Table) -> str:
        blocks = [
            heading(2, table.name, self.anchors[("table", table.name.casefold())]),
            "Hosted by " + page.obj(table.ref) + ".",
        ]
        if table.is_auto_date:
            blocks.append("This date table is created by time intelligence and does not have its own page.")
        for measure in table.measures:
            blocks.append(self._measure_block(page, measure))
        return "\n\n".join(blocks)

    def _measure_block(self, page: Page, measure: Measure) -> str:
        key = ("measure", measure.table.casefold(), measure.name.casefold())
        rows: list[list[object]] = [
            ["Table", Raw(page.obj(ObjectRef("table", measure.table)))],
            ["Hidden", "Yes" if measure.is_hidden else "No"],
        ]
        if measure.format_string:
            rows.append(["Format", Raw(html_code(measure.format_string))])
        if measure.display_folder:
            rows.append(["Display folder", Raw(html_code(measure.display_folder))])
        if measure.description:
            rows.append(["Description", measure.description])
        if measure.lineage_tag:
            rows.append(["Lineage tag", Raw(html_code(measure.lineage_tag))])
        flags = [flag for flag in measure.flags if flag != "isHidden"]
        if flags:
            rows.append(["Flags", ", ".join(flags)])
        annotations = _notable_annotations(measure.annotations)
        if annotations:
            rows.append(["Annotations", _annotations_cell(annotations)])
        resolved, bad, local = _split_refs(measure.references)
        dependents = self.model.dependents_of(measure.ref)
        blocks = [
            heading(3, measure.name, self.anchors[key]),
            markdown_table(["Property", "Value"], rows),
        ]
        if measure.format_string_expression:
            blocks.append("**Format string expression**\n\n" + fenced_code(measure.format_string_expression, "dax"))
        expression = measure.expression if measure.expression and measure.expression.strip() else ""
        blocks.append("**Expression**\n\n" + (
            fenced_code(expression, "dax") if expression else "The measure does not store an expression."
        ))
        blocks.append("**Dependencies**\n\n" + bullets(page.format_ref(ref) for ref in resolved))
        blocks.append("**Referenced by**\n\n" + bullets(
            f"{page.obj(item.source)} ({item.via})" for item in dependents
        ))
        if self.report is not None:
            blocks.append(self._measure_report_uses(page, measure))
        blocks.append("**Unresolved or ambiguous references**\n\n" + bullets(page.format_ref(ref) for ref in bad))
        if local:
            blocks.append("**Names local to the expression**\n\n" + bullets(code_span(ref.raw) for ref in local))
        return "\n\n".join(blocks)

    def _sources_page(self) -> str:
        page = self.page("root", "Sources.md")
        blocks = [
            page.nav(),
            heading(1, "Sources"),
            "Physical sources read by Power Query in "
            + f"**{self.model.name}**. Connection secrets are not stored in TMDL. "
            + "Password-like text in an extracted argument is redacted.",
            self._source_inventory(page),
            self._source_details(page),
            self._power_query(page),
            self._shared_expressions(page),
        ]
        return document(blocks)

    def _source_inventory(self, page: Page) -> str:
        show_entity = any(source.details.get("entity") for source, _records in self.source_groups)
        rows = []
        for (source, records), _title in zip(self.source_groups, self.source_titles):
            row: list[object] = [
                Raw(page.source_link(source, _source_title(source, skip=frozenset({"entity"})))),
                source.connector + (" (generic fallback)" if source.is_generic else ""),
            ]
            if show_entity:
                entity = source.details.get("entity", "")
                row.append(Raw(html_code(entity)) if entity else "")
            row.append(_details_cell(source, skip=frozenset({"entity"}) if show_entity else frozenset()))
            row.append(_consumers_cell(page, records))
            rows.append(row)
        headers = ["Source", "Connector"]
        if show_entity:
            headers.append("Entity")
        headers.extend(["Details", "Used by"])
        return (
            heading(2, "Inventory")
            + "\n\nDistinct sources are grouped by connector, arguments, navigation, and format.\n\n"
            + markdown_table(headers, rows)
        )

    def _source_details(self, page: Page) -> str:
        blocks = [heading(2, "Source details")]
        if not self.source_groups:
            blocks.append("No physical sources were extracted.")
            return "\n\n".join(blocks)
        for (source, records), title in zip(self.source_groups, self.source_titles):
            blocks.append(self._one_source(page, source, records, title))
        return "\n\n".join(blocks)

    def _one_source(self, page: Page, source: DataSource, records: list[DataSource], title: str) -> str:
        key = ("source", source.identity)
        rows: list[list[object]] = [
            ["Connector", Raw(html_code(source.connector))],
            ["Generic fallback", "Yes" if source.is_generic else "No"],
        ]
        if source.format:
            rows.append(["Format", Raw(html_code(source.format))])
        for key_name, value in _ordered_details(source.details):
            rows.append([key_name, Raw(html_code(value))])
        consumers = []
        for record in sorted(records, key=lambda item: (item.table.casefold(), item.partition.casefold(), item.step)):
            query = page.query_link(record)
            consumers.append([
                page.consumer_table(record),
                Raw(html_code(record.partition)),
                Raw(html_code(record.step)),
                Raw(query) if query else "",
            ])
        navigation = [
            ", ".join(
                f"{code_span(name)}={code_span(value) if value else '(empty)'}"
                for name, value in step.items()
            ) or "(empty)"
            for step in source.navigation
        ]
        return "\n\n".join([
            heading(3, title, self.anchors[key]),
            markdown_table(["Property", "Value"], rows),
            "**Arguments**\n\n" + _arguments_block(source.arguments),
            "**Navigation**\n\n" + numbered(navigation),
            "**Consumed by**\n\n" + markdown_table(
                ["Table or expression", "Partition", "Step", "Query"],
                consumers,
            ),
        ])

    def _power_query(self, page: Page) -> str:
        blocks = [heading(2, "Power Query expressions", "power-query")]
        found = False
        for table in self.model.tables:
            for partition in table.partitions:
                if partition.language != "m":
                    continue
                found = True
                blocks.append(self._m_block(page, table, partition))
        if not found:
            blocks.append("No Power Query partitions are stored in the model.")
        return "\n\n".join(blocks)

    def _m_block(self, page: Page, table: Table, partition: Partition) -> str:
        key = ("m-block", table.name.casefold(), partition.name.casefold())
        where = page.obj(table.ref)
        partition_link = page.obj(partition.ref)
        refs = [page.query_name_link(name) for name in partition.query_references]
        body = [
            heading(3, f"{table.name} / {partition.name}", self.anchors[key]),
            f"Table: {where}. Partition: {partition_link}.",
        ]
        if partition.expression and partition.expression.strip():
            body.append(fenced_code(partition.expression, "powerquery"))
        else:
            body.append("No M expression is stored for this partition.")
        if refs:
            body.append("**Query references**\n\n" + bullets(refs))
        return "\n\n".join(body)

    def _shared_expressions(self, page: Page) -> str:
        if not self.model.expressions:
            return ""
        blocks = [heading(2, "Shared expressions")]
        for expression in self.model.expressions:
            key = ("expression", expression.name.casefold())
            language = "powerquery" if expression.kind in (None, "m") else "dax" if expression.kind == "dax" else ""
            rows: list[list[object]] = []
            if expression.kind:
                rows.append(["Kind", Raw(html_code(expression.kind))])
            if expression.description:
                rows.append(["Description", expression.description])
            part = [
                heading(3, expression.name, self.anchors[key]),
                markdown_table(["Property", "Value"], rows) if rows else "",
                fenced_code(expression.expression, language) if expression.expression.strip() else "No expression is stored.",
            ]
            if expression.query_references:
                part.append("**Query references**\n\n" + bullets(
                    page.query_name_link(name) for name in expression.query_references
                ))
            blocks.append("\n\n".join(block for block in part if block))
        return "\n\n".join(blocks)

    def _table_page(self, table: Table) -> str:
        page = self.page("table", f"Tables/{table.doc_filename}")
        key = ("table", table.name.casefold())
        intro = (
            f"{class_label(table.classification)} table with {plural(len(table.columns), 'column')}, "
            f"{plural(len(table.measures), 'measure')}, and {plural(len(table.partitions), 'partition')}."
        )
        if table.is_hidden:
            intro += " This table is hidden."
        if table.is_private:
            intro += " This table is private."
        if table.classification == TABLE_IMPORTED:
            origin = self._imported_source_links(page, table)
            if origin:
                intro += " Comes from " + origin + "."
        return document([
            page.nav(),
            heading(1, table.name, self.anchors[key]),
            intro,
            self._purpose(table),
            self._table_metadata(table),
            self._table_sources(page, table),
            self._columns(page, table),
            self._variations(page, table),
            self._hierarchies(table),
            self._partitions(page, table),
            self._table_measures(page, table),
            self._table_dependencies(page, table),
            self._table_relationships(page, table),
            self._table_report_uses(page, table) if self.report is not None else None,
        ])

    def _imported_source_links(self, page: Page, table: Table) -> str:
        links: list[str] = []
        seen: set[tuple] = set()
        for source in self.model.sources_for(table.name):
            if source.identity in seen:
                continue
            seen.add(source.identity)
            links.append(page.source_link(source))
        if not links:
            return ""
        if len(links) == 1:
            return links[0]
        if len(links) == 2:
            return f"{links[0]} and {links[1]}"
        return ", ".join(links[:-1]) + ", and " + links[-1]

    def _purpose(self, table: Table) -> str:
        if table.description:
            body = esc_description(table.description)
        else:
            body = "The model does not store a description for this table."
        return heading(2, "Purpose") + "\n\n" + body

    def _table_metadata(self, table: Table) -> str:
        rows: list[list[object]] = [
            ["Classification", class_label(table.classification)],
            ["Hidden", "Yes" if table.is_hidden else "No"],
            ["Private", "Yes" if table.is_private else "No"],
        ]
        if table.lineage_tag:
            rows.append(["Lineage tag", Raw(html_code(table.lineage_tag))])
        rows.append(["Source file", Raw(html_code(table.file_path))])
        flags = [flag for flag in table.flags if flag not in ("isHidden", "isPrivate")]
        if flags:
            rows.append(["Flags", ", ".join(flags)])
        if table.annotations:
            rows.append(["Annotations", _annotations_cell(table.annotations)])
        for key, value in table.properties.items():
            if key in ("lineageTag", "description"):
                continue
            rows.append([key, Raw(html_code(value))])
        return heading(2, "Metadata") + "\n\n" + markdown_table(["Property", "Value"], rows)

    def _columns(self, page: Page, table: Table) -> str:
        blocks = [heading(2, "Columns")]
        if not table.columns:
            blocks.append("None.")
            return "\n\n".join(blocks)
        show_format = any(column.format_string for column in table.columns)
        show_summary = any(column.summarize_by and column.summarize_by.lower() != "none" for column in table.columns)
        show_folder = any(column.display_folder for column in table.columns)
        show_category = any(column.data_category for column in table.columns)
        show_sort = any(column.sort_by_column for column in table.columns)
        show_description = any(column.description for column in table.columns)
        show_annotations = any(_notable_annotations(column.annotations) for column in table.columns)
        show_flags = any(_extra_flags(column) for column in table.columns)
        omitted_default = any(
            (annotation.name, annotation.value) in _SKIP_COLUMN_ANNOTATIONS
            for column in table.columns
            for annotation in column.annotations
        )
        headers = ["Name", "Kind", "Data type", "Source column", "Hidden"]
        if show_format:
            headers.append("Format")
        if show_summary:
            headers.append("Summarize by")
        if show_folder:
            headers.append("Display folder")
        if show_category:
            headers.append("Data category")
        if show_sort:
            headers.append("Sort by")
        if show_description:
            headers.append("Description")
        if show_flags:
            headers.append("Flags")
        if show_annotations:
            headers.append("Annotations")
        rows = []
        for column in table.columns:
            anchor = self.anchors[("column", table.name.casefold(), column.name.casefold())]
            row: list[object] = [
                Raw(f'<a id="{anchor}"></a>{html_code(column.name)}'),
                _COLUMN_KINDS.get(column.kind, column.kind),
                Raw(html_code(column.data_type)) if column.data_type else "",
                Raw(html_code(column.source_column)) if column.source_column else "",
                "Yes" if column.is_hidden else "No",
            ]
            if show_format:
                row.append(Raw(html_code(column.format_string)) if column.format_string else "")
            if show_summary:
                row.append(column.summarize_by or "")
            if show_folder:
                row.append(Raw(html_code(column.display_folder)) if column.display_folder else "")
            if show_category:
                row.append(Raw(html_code(column.data_category)) if column.data_category else "")
            if show_sort:
                row.append(Raw(html_code(column.sort_by_column)) if column.sort_by_column else "")
            if show_description:
                row.append(column.description or "")
            if show_flags:
                row.append(", ".join(_extra_flags(column)))
            if show_annotations:
                notable = _notable_annotations(column.annotations)
                row.append(_annotations_cell(notable) if notable else "")
            rows.append(row)
        blocks.append(markdown_table(headers, rows))
        if omitted_default:
            blocks.append(
                "Annotation " + code_span("SummarizationSetBy = Automatic")
                + " is omitted. Other annotations are listed in the table."
            )
        for column in table.columns:
            if column.expression is None:
                continue
            resolved, bad, local = _split_refs(column.references)
            part = [
                heading(3, column.name),
                "**Expression**\n\n" + fenced_code(column.expression, "dax"),
                "**Dependencies**\n\n" + bullets(page.format_ref(ref) for ref in resolved),
            ]
            if bad:
                part.append("**Unresolved or ambiguous references**\n\n" + bullets(
                    page.format_ref(ref) for ref in bad
                ))
            if local:
                part.append("**Names local to the expression**\n\n" + bullets(code_span(ref.raw) for ref in local))
            blocks.append("\n\n".join(part))
        return "\n\n".join(blocks)

    def _variations(self, page: Page, table: Table) -> str:
        rows = []
        for column in table.columns:
            column_key = ("column", table.name.casefold(), column.name.casefold())
            column_link = md_link(column.name, f"#{self.anchors[column_key]}")
            for variation in column.variations:
                rel = next(
                    (item for item in self.model.relationships if item.name == variation.relationship),
                    None,
                )
                if rel is None:
                    target: object = ""
                    relationship: object = Raw(code_span(variation.relationship or ""))
                else:
                    other_table, other_column = _other_end(rel, table.name)
                    target = Raw(page.obj(ObjectRef("column", other_table, other_column)))
                    relationship = Raw(page.relationship_name(rel))
                rows.append([
                    Raw(column_link),
                    Raw(html_code(variation.name)),
                    relationship,
                    target,
                    Raw(html_code(variation.default_hierarchy)) if variation.default_hierarchy else "",
                    "Yes" if variation.is_default else "No",
                ])
        if not rows:
            return ""
        return (
            heading(2, "Date variations")
            + "\n\nVariations connect a date column to an auto-generated date table.\n\n"
            + markdown_table(
                ["Column", "Variation", "Relationship", "Date table", "Default hierarchy", "Default"],
                rows,
            )
        )

    def _hierarchies(self, table: Table) -> str:
        if not table.hierarchies:
            return ""
        rows = []
        for hierarchy in table.hierarchies:
            levels = Raw(" &gt; ".join(
                html_code(level.column or level.name) for level in hierarchy.levels
            ))
            rows.append([
                Raw(html_code(hierarchy.name)),
                levels,
                "Yes" if hierarchy.is_hidden else "No",
                hierarchy.description or "",
            ])
        return heading(2, "Hierarchies") + "\n\n" + markdown_table(
            ["Hierarchy", "Levels", "Hidden", "Description"],
            rows,
        )

    def _partitions(self, page: Page, table: Table) -> str:
        blocks = [heading(2, "Partitions")]
        if not table.partitions:
            blocks.append("None.")
            return "\n\n".join(blocks)
        for partition in table.partitions:
            blocks.append(self._partition(page, table, partition))
        return "\n\n".join(blocks)

    def _partition(self, page: Page, table: Table, partition: Partition) -> str:
        key = ("partition", table.name.casefold(), partition.name.casefold())
        rows: list[list[object]] = [["Source type", source_type_label(partition)]]
        if partition.mode:
            rows.append(["Mode", Raw(html_code(partition.mode))])
        if partition.query_group:
            rows.append(["Query group", Raw(html_code(partition.query_group))])
        if partition.description:
            rows.append(["Description", partition.description])
        annotations = _notable_annotations(partition.annotations)
        if annotations:
            rows.append(["Annotations", _annotations_cell(annotations)])
        for prop, value in partition.properties.items():
            if prop in ("mode", "queryGroup", "description", "lineageTag"):
                continue
            rows.append([prop, Raw(html_code(value))])
        blocks = [
            heading(3, partition.name, self.anchors[key]),
            markdown_table(["Property", "Value"], rows),
        ]
        if partition.expression and partition.expression.strip():
            language = "dax" if partition.language == "dax" else "powerquery" if partition.language == "m" else ""
            blocks.append(fenced_code(partition.expression, language))
        else:
            blocks.append("No M or DAX expression is stored for this partition.")
        if partition.language == "m" and partition.query_references:
            blocks.append("**Query references**\n\n" + bullets(
                page.query_name_link(name) for name in partition.query_references
            ))
        if partition.language == "dax":
            resolved, bad, local = _split_refs(partition.references)
            blocks.append("**Dependencies**\n\n" + bullets(page.format_ref(ref) for ref in resolved))
            if bad:
                blocks.append("**Unresolved or ambiguous references**\n\n" + bullets(
                    page.format_ref(ref) for ref in bad
                ))
            if local:
                blocks.append("**Names local to the expression**\n\n" + bullets(
                    code_span(ref.raw) for ref in local
                ))
        if partition.language == "m" and any(
            source.table.casefold() == table.name.casefold()
            and source.partition.casefold() == partition.name.casefold()
            for source in self.model.sources
        ):
            blocks.append(
                "Physical sources for this partition are listed under "
                + md_link("Sources", "#physical-sources") + "."
            )
        return "\n\n".join(blocks)

    def _table_sources(self, page: Page, table: Table) -> str:
        sources: list[DataSource] = []
        seen: set[tuple] = set()
        for source in self.model.sources_for(table.name):
            marker = (source.identity, source.partition.casefold())
            if marker in seen:
                continue
            seen.add(marker)
            sources.append(source)
        show_entity = any(source.details.get("entity") for source in sources)
        rows = []
        for source in sources:
            row: list[object] = [
                Raw(html_code(source.partition)),
                Raw(html_code(source.connector)),
            ]
            if show_entity:
                entity = source.details.get("entity", "")
                row.append(Raw(html_code(entity)) if entity else "")
            row.extend([
                _details_cell(source, skip=frozenset({"entity"}) if show_entity else frozenset()),
                Raw(page.source_link(source, self._title_for(source))),
            ])
            rows.append(row)
        headers = ["Partition", "Connector"]
        if show_entity:
            headers.append("Entity")
        headers.extend(["Details", "Source record"])
        return (
            heading(2, "Sources", "physical-sources")
            + "\n\n"
            + markdown_table(headers, rows)
        )

    def _table_measures(self, page: Page, table: Table) -> str:
        if not table.measures:
            return heading(2, "Measures") + "\n\nNone."
        show_format = any(measure.format_string for measure in table.measures)
        show_folder = any(measure.display_folder for measure in table.measures)
        show_description = any(measure.description for measure in table.measures)
        headers = ["Measure", "Hidden", "Resolved", "Unresolved"]
        if show_format:
            headers.insert(1, "Format")
        if show_folder:
            headers.append("Display folder")
        if show_description:
            headers.append("Description")
        rows = []
        for measure in table.measures:
            resolved, bad, _local = _split_refs(measure.references)
            row: list[object] = [
                Raw(page.measure_link(measure)),
                "Yes" if measure.is_hidden else "No",
                len(resolved),
                len(bad),
            ]
            if show_format:
                row.insert(1, Raw(html_code(measure.format_string)) if measure.format_string else "")
            if show_folder:
                row.append(Raw(html_code(measure.display_folder)) if measure.display_folder else "")
            if show_description:
                row.append(measure.description or "")
            rows.append(row)
        return (
            heading(2, "Measures")
            + "\n\nExpressions and dependency detail are on the "
            + md_link("measures page", page.root("Measures.md")) + ".\n\n"
            + markdown_table(headers, rows)
        )

    def _table_dependencies(self, page: Page, table: Table) -> str:
        outbound = [
            item for item in self.model.dependencies
            if _same_table(item.source, table.name) and _external(item.target, table.name)
        ]
        inbound = [
            item for item in self.model.dependencies
            if _same_table(item.target, table.name) and _external(item.source, table.name)
        ]
        bad_rows = []
        for source, refs in _expression_references(self.model, table):
            for ref in refs:
                if ref.status in _BAD_STATUSES:
                    bad_rows.append([
                        Raw(page.obj(source)),
                        Raw(html_code(ref.raw)),
                        "Ambiguous" if ref.status == REF_AMBIGUOUS else "Unresolved",
                        _ref_detail(page, ref),
                    ])
        depends = markdown_table(
            ["From", "To", "Via"],
            [[Raw(page.obj(item.source)), Raw(page.obj(item.target)), item.via] for item in outbound],
        )
        referenced = markdown_table(
            ["Object", "Referenced by", "Via"],
            [[Raw(page.obj(item.target)), Raw(page.obj(item.source)), item.via] for item in inbound],
        )
        return "\n\n".join([
            heading(2, "Dependencies"),
            "This section lists dependencies that cross to another table or shared expression. "
            "References that stay inside this table are listed on each calculated column and partition, "
            "and on the " + md_link("measures page", page.root("Measures.md")) + ".",
            heading(3, "Depends on") + "\n\n" + depends,
            heading(3, "Referenced by") + "\n\n" + referenced,
            heading(3, "Unresolved or ambiguous references") + "\n\n" + markdown_table(
                ["Object", "Reference", "Status", "Detail"],
                bad_rows,
            ),
        ])

    def _table_relationships(self, page: Page, table: Table) -> str:
        outbound, inbound = self.model.relationships_for(table.name)
        catalog = md_link("model page", page.anchored(page.root("Model.md"), _STATIC["relationships"]))
        return "\n\n".join([
            heading(2, "Relationships"),
            "Outbound relationships leave this table. Inbound relationships point at it. "
            f"Relationships between authored tables are collected on the {catalog}.",
            heading(3, "Outbound") + "\n\n" + self._relationship_table(page, outbound, emit_anchors=False),
            heading(3, "Inbound") + "\n\n" + self._relationship_table(page, inbound, emit_anchors=False),
        ])

    def _relationship_table(
        self,
        page: Page,
        relationships: list[Relationship],
        *,
        emit_anchors: bool,
        show_names: bool = True,
    ) -> str:
        if not relationships:
            return "None."
        show_security = any(
            (rel.option("securityFilteringBehavior") or "oneDirection") != "oneDirection"
            for rel in relationships
        )
        show_integrity = any(
            (rel.option("relyOnReferentialIntegrity") or "false").lower() == "true"
            for rel in relationships
        )
        show_date = show_names or any(
            (rel.join_on_date_behavior or "dateAndTime") != "dateAndTime" for rel in relationships
        )
        show_notes = show_names or any(_relationship_notes(rel) for rel in relationships)
        headers = []
        if show_names:
            headers.append("Relationship")
        headers.extend(["From", "To", "Cardinality", "Active", "Cross-filter"])
        if show_date:
            headers.append("Date join")
        if show_security:
            headers.append("Security filter")
        if show_integrity:
            headers.append("Referential integrity")
        if show_notes:
            headers.append("Notes")
        rows = [
            self._relationship_row(
                page, rel, emit_anchors, show_names, show_date, show_security, show_integrity, show_notes,
            )
            for rel in relationships
        ]
        return markdown_table(headers, rows)

    def _relationship_row(
        self,
        page: Page,
        rel: Relationship,
        emit_anchors: bool,
        show_names: bool,
        show_date: bool,
        show_security: bool,
        show_integrity: bool,
        show_notes: bool,
    ) -> list[object]:
        from_cell = page.column_ref(rel.from_table, rel.from_column)
        if emit_anchors and not show_names:
            anchor = self.anchors[self.rel_keys[id(rel)]]
            from_cell = f'<a id="{anchor}"></a>{from_cell}'
        row: list[object] = []
        if show_names:
            row.append(page.relationship_cell(rel, emit_anchors))
        row.extend([
            Raw(from_cell),
            Raw(page.column_ref(rel.to_table, rel.to_column)),
            f"{_word(rel.from_cardinality)} to {_word(rel.to_cardinality)}",
            "Inactive" if not rel.is_active else "Active",
            _CROSS_FILTER.get(rel.cross_filtering_behavior, rel.cross_filtering_behavior),
        ])
        if show_date:
            join = rel.join_on_date_behavior or ""
            row.append(_DATE_JOIN.get(join, join))
        if show_security:
            security = rel.option("securityFilteringBehavior") or ""
            row.append(_CROSS_FILTER.get(security, security))
        if show_integrity:
            row.append("Yes" if (rel.option("relyOnReferentialIntegrity") or "").lower() == "true" else "No")
        if show_notes:
            row.append(_relationship_notes(rel))
        return row

    def _title_for(self, source: DataSource) -> str:
        for (candidate, _records), title in zip(self.source_groups, self.source_titles):
            if candidate.identity == source.identity:
                return title
        return source.connector

    def _visuals_index(self) -> str:
        page = self.page("root", "Visuals.md")
        report = self.report
        assert report is not None
        visible_pages = [item for item in report.pages if not item.hidden]
        hidden_pages = [item for item in report.pages if item.hidden]
        intro = (
            f"Field usage for visuals in {self.report_location()}. "
            f"{plural(len(report.visuals), 'visual')} bind tables, columns, or measures. "
            "Text boxes, images, shapes, buttons, and groups are counted on each report page "
            "and do not have their own page."
        )
        blocks = [
            page.nav(),
            heading(1, "Visuals"),
            intro,
            heading(2, "Visible pages") + "\n\n" + bullets(
                md_link(item.display_name, page.report_page_href(item))
                for item in visible_pages
            ),
        ]
        if hidden_pages:
            blocks.append(
                f"{plural(len(hidden_pages), 'hidden report page')} "
                f"{'is' if len(hidden_pages) == 1 else 'are'} listed in "
                f"{md_link('Hidden pages', '#hidden-pages')}."
            )
        blocks.append(self._report_filter_section(page))
        for report_page in visible_pages:
            blocks.append(self._visual_page_section(page, report_page))
        if hidden_pages:
            blocks.append(
                heading(2, "Hidden pages", "hidden-pages")
                + "\n\n"
                + bullets(
                    md_link(item.display_name, page.report_page_href(item))
                    for item in hidden_pages
                )
            )
            for report_page in hidden_pages:
                blocks.append(self._visual_page_section(page, report_page))
        return document(blocks)

    def _report_filter_section(self, page: Page) -> str:
        report = self.report
        assert report is not None
        body = _scope_filter_table(page, self, [("Report", binding) for binding in report.filters])
        if body == "None.":
            body = "This report has no report-level filters."
        return heading(2, "Report filters", "report-filters") + "\n\n" + body

    def _visual_page_section(self, page: Page, report_page: ReportPage) -> str:
        key = ("report-page", report_page.name)
        status = "This report page is hidden in view mode." if report_page.hidden else "This report page is visible."
        if report_page.filters:
            filters = "Filters added on this page:\n\n" + _scope_filter_table(
                page, self, [("Page", binding) for binding in report_page.filters],
            )
        else:
            filters = "This page adds no filters of its own."
        if report_page.visuals:
            rows = [
                [
                    Raw(md_link(visual.heading, page.visual_href(visual))),
                    visual.type_label,
                    _role_summary(visual),
                ]
                for visual in report_page.visuals
            ]
            visual_table = markdown_table(["Visual", "Type", "How fields are used"], rows)
        else:
            visual_table = "No visual on this page binds a model field."
        chrome = report_page.chrome_sentence()
        return "\n\n".join(part for part in (
            heading(2, report_page.display_name, self.anchors[key]),
            status,
            filters,
            visual_table,
            chrome,
        ) if part)

    def _visual_page(self, report_page: ReportPage, visual: Visual) -> str:
        page = self.page("visual", visual.doc_rel)
        key = ("visual", report_page.name, visual.name)
        page_link = md_link(report_page.display_name, page.report_page_href(report_page))
        intro = f"{visual.type_label} on {page_link}."
        if report_page.hidden:
            intro += " This report page is hidden in view mode."
        else:
            intro += " This report page is visible."
        if visual.title and visual.title_hidden:
            intro += f" The title {code_span(visual.title)} is hidden."
        blocks = [
            page.nav(),
            heading(1, visual.heading, self.anchors[key]),
            intro,
            self._visual_metadata(page, report_page, visual, page_link),
            heading(2, "Fields") + "\n\n"
            + "Tables, columns, and measures on this visual, and the role each one plays.\n\n"
            + _field_table(page, self, visual.bindings),
            self._inherited_filters(page, report_page),
        ]
        return document(blocks)

    def _visual_metadata(self, page: Page, report_page: ReportPage, visual: Visual, page_link: str) -> str:
        rows: list[list[object]] = [
            ["Type", Raw(f"{visual.type_label} ({html_code(visual.visual_type)})")],
            ["Report page", Raw(page_link)],
            ["Page status", "Hidden in view mode" if report_page.hidden else "Visible"],
        ]
        if visual.title:
            title = visual.title + (" (hidden)" if visual.title_hidden else "")
            rows.append(["Title", title])
        rows.append(["Visual id", Raw(html_code(visual.name))])
        rows.append([
            "Definition",
            Raw(html_code(f"pages/{report_page.name}/visuals/{visual.name}/visual.json")),
        ])
        return heading(2, "Metadata") + "\n\n" + markdown_table(["Property", "Value"], rows)

    def _inherited_filters(self, page: Page, report_page: ReportPage) -> str | None:
        report = self.report
        assert report is not None
        scoped = [("Page", binding) for binding in report_page.filters]
        scoped.extend(("Report", binding) for binding in report.filters)
        if not scoped:
            return None
        return (
            heading(2, "Page and report filters")
            + "\n\nFilters on this report page and on the report also limit the visual.\n\n"
            + _scope_filter_table(page, self, scoped)
        )

    def _table_report_uses(self, page: Page, table: Table) -> str:
        rows: list[list[object]] = []
        fields = [("column", column.name) for column in table.columns]
        fields.extend(("measure", measure.name) for measure in table.measures)
        for kind, name in fields:
            for scope, binding, visual, report_page in self._uses_for(kind, table.name, name):
                rows.append([
                    Raw(page.obj(self._binding_ref(binding))),
                    Raw(self._where_used(page, scope, visual, report_page)),
                    _describe_use(scope, binding),
                ])
        body = markdown_table(["Field", "Where", "How used"], rows) if rows else "None."
        return (
            heading(2, "Used in the report")
            + "\n\nWhere columns and measures from this table appear in the report.\n\n"
            + body
        )

    def _measure_report_uses(self, page: Page, measure: Measure) -> str:
        uses = self._uses_for("measure", measure.table, measure.name)
        if not uses:
            body = "None."
        else:
            body = bullets(
                f"{self._where_used(page, scope, visual, report_page)}: {_describe_use(scope, binding)}"
                for scope, binding, visual, report_page in uses
            )
        return "**Used in the report**\n\n" + body

    def _uses_for(self, kind: str, table: str, name: str) -> list[tuple]:
        index = self._ensure_use_index()
        return index.get((kind, table.casefold(), name.casefold()), [])

    def _ensure_use_index(self) -> dict[tuple[str, str, str], list[tuple]]:
        if self._use_index is not None:
            return self._use_index
        index: dict[tuple[str, str, str], list[tuple]] = {}

        def add(binding: FieldBinding, visual: Visual | None, report_page: ReportPage | None, scope: str) -> None:
            if not binding.name and not binding.table:
                return
            key = (binding.kind, binding.table.casefold(), binding.name.casefold())
            index.setdefault(key, []).append((scope, binding, visual, report_page))

        report = self.report
        assert report is not None
        for report_page in report.pages:
            for visual in report_page.visuals:
                for binding in visual.bindings:
                    add(binding, visual, report_page, "visual")
        for report_page in report.pages:
            for binding in report_page.filters:
                add(binding, None, report_page, "page")
        for binding in report.filters:
            add(binding, None, None, "report")
        self._use_index = index
        return index

    def _binding_ref(self, binding: FieldBinding) -> ObjectRef:
        table = self.model.table(binding.table) if binding.table else None
        if table is None:
            kind = binding.kind if binding.kind in ("column", "measure") else "column"
            return ObjectRef(kind, binding.table, binding.name)
        if binding.kind == "measure":
            for measure in table.measures:
                if measure.name.casefold() == binding.name.casefold():
                    return measure.ref
            return ObjectRef("measure", table.name, binding.name)
        if binding.name:
            for column in table.columns:
                if column.name.casefold() == binding.name.casefold():
                    return column.ref
            return ObjectRef("column", table.name, binding.name)
        return table.ref

    def _where_used(
        self,
        page: Page,
        scope: str,
        visual: Visual | None,
        report_page: ReportPage | None,
    ) -> str:
        if scope == "visual" and visual is not None and report_page is not None:
            return (
                f"{md_link(visual.heading, page.visual_href(visual))} on "
                f"{md_link(report_page.display_name, page.report_page_href(report_page))}"
            )
        if scope == "page" and report_page is not None:
            return f"Page filter on {md_link(report_page.display_name, page.report_page_href(report_page))}"
        return md_link("Report filter", page.anchored(page.root("Visuals.md"), ("static", "report-filters")))


def _role_summary(visual: Visual) -> str:
    seen: list[str] = []
    for binding in visual.bindings:
        if binding.how not in seen:
            seen.append(binding.how)
    return ", ".join(seen)


def _describe_use(scope: str, binding: FieldBinding) -> str:
    if scope in ("page", "report"):
        return binding.detail or "On the filter pane"
    parts = [binding.how]
    if binding.aggregation:
        parts.append(binding.aggregation)
    extras = []
    if binding.detail:
        extras.append(binding.detail)
    if binding.inactive:
        extras.append("Inactive drill level")
    if binding.sort:
        extras.append(binding.sort)
    text = ", ".join(parts)
    if extras:
        text += ". " + ". ".join(extras)
    return text


def _field_table(page: Page, renderer: Renderer, bindings: list[FieldBinding]) -> str:
    if not bindings:
        return "None."
    show_agg = any(binding.aggregation for binding in bindings)
    show_as = any(binding.display_name for binding in bindings)
    show_notes = any(binding.notes() for binding in bindings)
    headers = ["How used", "Kind", "Object"]
    if show_agg:
        headers.append("Aggregation")
    if show_as:
        headers.append("Shown as")
    if show_notes:
        headers.append("Notes")
    rows: list[list[object]] = []
    for binding in bindings:
        row: list[object] = [
            binding.how,
            "Measure" if binding.kind == "measure" else "Column",
            Raw(page.obj(renderer._binding_ref(binding))),
        ]
        if show_agg:
            row.append(binding.aggregation or "")
        if show_as:
            row.append(binding.display_name or "")
        if show_notes:
            row.append(binding.notes())
        rows.append(row)
    return markdown_table(headers, rows)


def _scope_filter_table(page: Page, renderer: Renderer, scoped: list[tuple[str, FieldBinding]]) -> str:
    if not scoped:
        return "None."
    show_as = any(binding.display_name for _scope, binding in scoped)
    headers = ["Scope", "Object"]
    if show_as:
        headers.append("Shown as")
    headers.append("Condition")
    rows: list[list[object]] = []
    for scope, binding in scoped:
        row = [scope, Raw(page.obj(renderer._binding_ref(binding)))]
        if show_as:
            row.append(binding.display_name or "")
        row.append(binding.detail)
        rows.append(row)
    return markdown_table(headers, rows)


class Page:
    """Relative links for one generated file."""

    def __init__(self, renderer: Renderer, location: str, current_file: str):
        self.renderer = renderer
        self.location = location
        self.current_file = current_file

    def root(self, filename: str) -> str:
        if self.location == "root" and self.current_file == filename:
            return "#"
        if self.location == "visual":
            return f"../../{filename}"
        if self.location == "table":
            return f"../{filename}"
        return filename

    def table_file(self, table: Table) -> str:
        if self.location == "table":
            if self.current_file == f"Tables/{table.doc_filename}":
                return "#"
            return table.doc_filename
        if self.location == "visual":
            return f"../../Tables/{table.doc_filename}"
        return f"Tables/{table.doc_filename}"

    def visual_href(self, visual: Visual) -> str:
        if self.current_file == visual.doc_rel:
            return "#"
        if self.location == "visual":
            return f"../../{visual.doc_rel}"
        if self.location == "table":
            return f"../{visual.doc_rel}"
        return visual.doc_rel

    def report_page_href(self, report_page: ReportPage) -> str:
        return self.anchored(self.root("Visuals.md"), ("report-page", report_page.name))

    def nav(self) -> str:
        links = [
            md_link("Overview", self.root("README.md")),
            md_link("Model", self.root("Model.md")),
            md_link("Measures", self.root("Measures.md")),
            md_link("Sources", self.root("Sources.md")),
        ]
        if self.renderer.report is not None:
            links.append(md_link("Visuals", self.root("Visuals.md")))
        return " · ".join(links)

    def anchored(self, href: str, key: tuple) -> str:
        anchor = self.renderer.anchors.get(key, key[-1] if isinstance(key[-1], str) else "")
        if isinstance(key, tuple) and key and key[0] == "static":
            anchor = str(key[1])
        if href in ("", "#"):
            return f"#{anchor}"
        return f"{href}#{anchor}"

    def obj(self, ref: ObjectRef) -> str:
        renderer = self.renderer
        if ref.kind == "expression":
            key = ("expression", ref.name.casefold())
            if key not in renderer.anchors:
                return code_span(ref.name or ref.label)
            return md_link(ref.name, self.anchored(self.root("Sources.md"), key))
        if ref.kind == "measure":
            key = ("measure", ref.table.casefold(), ref.name.casefold())
            if key not in renderer.anchors:
                return code_span(ref.label)
            return md_link(ref.label, self.anchored(self.root("Measures.md"), key))
        table = renderer.model.table(ref.table) if ref.table else None
        if table is None:
            return code_span(ref.label)
        table_key = ("table", table.name.casefold())
        if table.is_auto_date:
            label = table.name if ref.kind == "table" else ref.label
            return code_span(label)
        if ref.kind == "table":
            return md_link(table.name, self.anchored(self.table_file(table), table_key))
        key = (ref.kind, table.name.casefold(), ref.name.casefold())
        if key not in renderer.anchors:
            return code_span(ref.label)
        return md_link(ref.label, self.anchored(self.table_file(table), key))

    def column_ref(self, table_name: str, column_name: str) -> str:
        if column_name:
            return self.obj(ObjectRef("column", table_name, column_name))
        if table_name:
            return self.obj(ObjectRef("table", table_name))
        return ""

    def measure_link(self, measure: Measure) -> str:
        key = ("measure", measure.table.casefold(), measure.name.casefold())
        return md_link(measure.name, self.anchored(self.root("Measures.md"), key))

    def relationship_link(self, rel: Relationship) -> str:
        return self.anchored(self.root("Model.md"), self.renderer.rel_keys[id(rel)])

    def relationship_name(self, rel: Relationship) -> str:
        if self.renderer._in_relationship_catalog(rel):
            return md_link(rel.name, self.relationship_link(rel))
        return html_code(rel.name)

    def relationship_cell(self, rel: Relationship, emit_anchor: bool) -> Raw:
        key = self.renderer.rel_keys[id(rel)]
        if emit_anchor:
            return Raw(f'<a id="{self.renderer.anchors[key]}"></a>{html_code(rel.name)}')
        return Raw(self.relationship_name(rel))

    def source_link(self, source: DataSource, title: str | None = None) -> str:
        key = ("source", source.identity)
        label = title or self.renderer._title_for(source)
        if key not in self.renderer.anchors:
            return code_span(label)
        return md_link(label, self.anchored(self.root("Sources.md"), key))

    def query_link(self, source: DataSource) -> str:
        if source.table:
            key = ("m-block", source.table.casefold(), source.partition.casefold())
            label = f"{source.table} / {source.partition}"
        else:
            key = ("expression", source.partition.casefold())
            label = source.partition
        if key not in self.renderer.anchors:
            return ""
        return md_link(label, self.anchored(self.root("Sources.md"), key))

    def query_name_link(self, name: str) -> str:
        table = self.renderer.model.table(name)
        if table is not None:
            return self.obj(table.ref)
        for expression in self.renderer.model.expressions:
            if expression.name.casefold() == name.casefold():
                return self.obj(ObjectRef("expression", "", expression.name))
        return code_span(name)

    def consumer_table(self, source: DataSource) -> Raw:
        if source.table:
            table = self.renderer.model.table(source.table)
            text = self.obj(table.ref) if table else code_span(source.table)
        else:
            text = self.query_name_link(source.partition)
        return Raw(text)

    def format_ref(self, ref: DaxReference) -> str:
        if ref.status == REF_RESOLVED and ref.target is not None:
            text = self.obj(ref.target)
            if ref.note and ref.note not in _ROUTINE_NOTES:
                text += f" ({esc_description(ref.note)})"
            return text
        raw = code_span(ref.raw)
        if ref.status == REF_AMBIGUOUS:
            candidates = ", ".join(self.obj(item) for item in ref.candidates) or "none"
            note = f"; {esc_description(ref.note)}" if ref.note else ""
            return f"{raw} (ambiguous; candidates: {candidates}{note})"
        if ref.status == REF_LOCAL:
            return f"{raw} (local to the expression)"
        note = f"; {esc_description(ref.note)}" if ref.note else ""
        return f"{raw} (unresolved{note})"


_STATIC = {
    "relationships": ("static", "relationships"),
    "diagnostics": ("static", "diagnostics"),
    "power-query": ("static", "power-query"),
}


def class_label(classification: str) -> str:
    return _CLASS_LABELS.get(classification, classification)


def source_type_label(partition: Partition) -> str:
    if partition.language == "m":
        return "Power Query (m)"
    if partition.language == "dax":
        return "Calculated (DAX)"
    return partition.source_type or "Unspecified"


def visibility(table: Table) -> str:
    labels = []
    if table.is_hidden:
        labels.append("Hidden")
    if table.is_private:
        labels.append("Private")
    return ", ".join(labels) if labels else "Visible"


def plural(count: int, noun: str) -> str:
    word = noun if count == 1 else noun + "s"
    return f"{count} {word}"


def esc_description(text: str) -> str:
    from .markdown import esc_prose
    return esc_prose(text)


_DESCRIPTION_ORDERED = re.compile(r"(\d+)([.)])(?=\s)")


_PUBLISHED_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def published_line(when: date) -> str:
    """Date stamp for the overview. The report itself has no version number.

    Month names are fixed in English so the line does not follow the machine locale.
    """
    month = _PUBLISHED_MONTHS[when.month - 1]
    return f"Documentation published {month} {when.day}, {when.year}."


def report_description_section(text: str) -> str | None:
    """Markdown for the overview description, or ``None`` when there is no text.

    The source file is plain text. Characters that Markdown would treat as
    formatting are escaped so the overview shows the description as written.
    A blank line separates paragraphs.
    """
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return None
    paragraphs: list[str] = []
    for part in re.split(r"\n\s*\n", normalized):
        lines = [_escape_description_line(line) for line in part.split("\n")]
        if any(line.strip() for line in lines):
            paragraphs.append("<br>\n".join(lines))
    if not paragraphs:
        return None
    return heading(2, "Report description") + "\n\n" + "\n\n".join(paragraphs)


def _escape_description_line(line: str) -> str:
    text = (
        line.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\\", "\\\\")
    )
    for char in "`*_[]":
        text = text.replace(char, "\\" + char)
    body = text.lstrip(" ")
    indent = text[: len(text) - len(body)]
    ordered = _DESCRIPTION_ORDERED.match(body)
    if ordered:
        body = ordered.group(1) + "\\" + ordered.group(2) + body[ordered.end():]
    elif body.startswith("#") or re.match(r"[-+]\s", body) or re.fullmatch(r"-{3,}", body):
        body = "\\" + body
    return indent + body


def _is_absolute_href(href: str) -> bool:
    return href.startswith("/") or href.startswith("\\\\") or (
        len(href) > 2 and href[1] == ":" and href[0].isalpha()
    )


def _remove_stale(root: Path, folder: str, expected: set[str], removed: list[str]) -> None:
    base = root / folder
    if not base.exists():
        return
    for path in sorted(base.rglob("*.md"), key=lambda item: item.as_posix().casefold()):
        rel = path.relative_to(root).as_posix()
        if rel in expected:
            continue
        first = path.read_text(encoding="utf-8-sig").splitlines()[:1]
        if first and first[0].strip() == MARKER:
            path.unlink()
            removed.append(rel)
    directories = [path for path in base.rglob("*") if path.is_dir()]
    for path in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        if not any(path.iterdir()):
            path.rmdir()


def _output_path(root: Path, rel: str) -> Path:
    candidate = Path(rel)
    if candidate.is_absolute() or rel.startswith(("/", "\\")) or ".." in candidate.parts:
        raise ValueError(f"refusing to write outside the output folder: {rel}")
    target = root.joinpath(*candidate.parts)
    resolved_root = root.resolve()
    resolved = target.resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise ValueError(f"refusing to write outside the output folder: {rel}")
    return target


_MARKDOWN_FILE_LINK = re.compile(r"\]\((?!file:)([^)\s]+\.md)(?:#([^)\s]+))?\)")


def _cursor_file_links(root: Path, source_rel: str, text: str) -> str:
    """Use file URIs for cross-page links so Cursor resolves nested pages.

    Cursor's Markdown editor can fail to resolve ``../../...`` links from a
    nested visual page. File URIs preserve the exact target while fragment-only
    links remain ordinary same-page anchors.
    """
    source = _output_path(root, source_rel)
    resolved_root = root.resolve()

    def replace(match: re.Match[str]) -> str:
        raw_path = unquote(match.group(1))
        target = (source.parent / raw_path).resolve()
        if target == resolved_root or resolved_root not in target.parents:
            return match.group(0)
        href = target.as_uri()
        fragment = match.group(2)
        if fragment:
            href += "#" + quote(unquote(fragment), safe="-._~")
        return f"]({href})"

    return _MARKDOWN_FILE_LINK.sub(replace, text)


def _refresh_order(tables: list[Table], query_order: list[str]) -> tuple[list[Table], bool]:
    rank = {name.casefold(): index for index, name in enumerate(query_order)}
    if not rank or any(table.name.casefold() not in rank for table in tables):
        return tables, False
    return sorted(tables, key=lambda table: rank[table.name.casefold()]), True


def _time_intelligence_enabled(model: SemanticModel) -> bool:
    for annotation in model.annotations:
        if annotation.name != "__PBI_TimeIntelligenceEnabled":
            continue
        return annotation.value.strip().casefold() not in ("", "0", "false")
    return False


def _notable_annotations(annotations: list[Annotation]) -> list[Annotation]:
    return [
        annotation for annotation in annotations
        if (annotation.name, annotation.value) not in _SKIP_COLUMN_ANNOTATIONS
    ]


def _annotations_cell(annotations: list[Annotation]) -> Raw:
    return Raw("<br>".join(
        f"{html_code(annotation.name)} = {html_code(annotation.value)}"
        for annotation in annotations
    ))


def _extra_flags(column: Column) -> list[str]:
    return [flag for flag in column.flags if flag != "isHidden"]


def _other_end(rel: Relationship, table_name: str) -> tuple[str, str]:
    if rel.from_table.casefold() == table_name.casefold():
        return rel.to_table, rel.to_column
    return rel.from_table, rel.from_column


def _ordered_details(details: dict[str, str]) -> list[tuple[str, str]]:
    def rank(key: str) -> tuple:
        try:
            position = _DETAIL_RANK.index(key)
        except ValueError:
            position = len(_DETAIL_RANK)
        return (position, key)
    return [
        (key, value)
        for key, value in sorted(details.items(), key=lambda item: rank(item[0]))
        if value != ""
    ]


def _details_cell(source: DataSource, skip: frozenset[str] = frozenset()) -> object:
    pairs = [(key, value) for key, value in _ordered_details(source.details) if key not in skip]
    if not pairs:
        return ""
    return Raw("<br>".join(f"{html_code(key)}={html_code(value)}" for key, value in pairs))


def _consumers_cell(page: Page, records: list[DataSource]) -> Raw:
    seen: set[tuple[str, str]] = set()
    bits = []
    ordered = sorted(records, key=lambda item: (item.table.casefold(), item.partition.casefold()))
    for source in ordered:
        key = (source.table.casefold(), source.partition.casefold())
        if key in seen:
            continue
        seen.add(key)
        if source.table:
            table = page.renderer.model.table(source.table)
            label = page.obj(table.ref) if table else code_span(source.table)
            bits.append(f"{label} / {html_code(source.partition)}")
        else:
            bits.append(page.query_name_link(source.partition))
    return Raw("<br>".join(bits))


def _arguments_block(arguments: list[str]) -> str:
    if not arguments:
        return "None."
    blocks = []
    for index, argument in enumerate(arguments, 1):
        if "\n" in argument or len(argument) > 160:
            blocks.append(f"**Argument {index}**\n\n" + fenced_code(argument, "text"))
        else:
            blocks.append(f"{index}. {code_span(argument)}")
    return "\n\n".join(blocks)


def _disambiguate_titles(groups: list[tuple[DataSource, list[DataSource]]]) -> list[str]:
    bases = [_source_title(source) for source, _records in groups]
    totals: dict[str, int] = {}
    for base in bases:
        totals[base] = totals.get(base, 0) + 1
    seen: dict[str, int] = {}
    titles = []
    for base in bases:
        if totals[base] == 1:
            titles.append(base)
            continue
        seen[base] = seen.get(base, 0) + 1
        titles.append(f"{base} ({seen[base]})")
    return titles


def _source_title(source: DataSource, skip: frozenset[str] = frozenset()) -> str:
    for key in ("entity", "server", "database", "url", "site", "path", "connection", "query"):
        if key in skip:
            continue
        value = source.details.get(key)
        if value:
            return f"{source.connector} ({key}={value})"
    return source.connector


def _split_refs(refs: list[DaxReference]) -> tuple[list[DaxReference], list[DaxReference], list[DaxReference]]:
    resolved = [ref for ref in refs if ref.status == REF_RESOLVED]
    bad = [ref for ref in refs if ref.status in _BAD_STATUSES]
    local = [ref for ref in refs if ref.status == REF_LOCAL]
    return resolved, bad, local


def _expression_references(model: SemanticModel, only: Table | None = None):
    tables = [only] if only is not None else model.tables
    for table in tables:
        for measure in table.measures:
            yield measure.ref, measure.references
        for column in table.columns:
            if column.expression is not None:
                yield column.ref, column.references
        for partition in table.partitions:
            if partition.language == "dax" and partition.references:
                yield partition.ref, partition.references


def _ref_detail(page: Page, ref: DaxReference) -> object:
    bits = []
    if ref.note and ref.note not in _ROUTINE_NOTES:
        from .markdown import esc_cell
        bits.append(esc_cell(ref.note))
    if ref.candidates:
        bits.append("Candidates: " + ", ".join(page.obj(item) for item in ref.candidates))
    if not bits:
        return ""
    return Raw("<br>".join(bits))


def _relationship_notes(rel: Relationship) -> str:
    notes = []
    for key in sorted(rel.extra_options):
        notes.append(f"{key}: {rel.extra_options[key]}")
    for annotation in rel.annotations:
        notes.append(f"{annotation.name}: {annotation.value}")
    if not rel.valid:
        notes.append("Endpoint does not match a table or column in the model")
    return "\n".join(notes)


def _word(value: str) -> str:
    return value or "unspecified"


def _same_table(ref: ObjectRef, table_name: str) -> bool:
    return ref.kind != "expression" and ref.table.casefold() == table_name.casefold()


def _external(ref: ObjectRef, table_name: str) -> bool:
    return not _same_table(ref, table_name)
