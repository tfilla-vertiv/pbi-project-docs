"""Intermediate model graph built from a TMDL semantic-model definition."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

TABLE_IMPORTED = "imported"
TABLE_CALCULATED = "calculated"
TABLE_AUTO_DATE = "auto-date"
TABLE_MIXED = "mixed"
TABLE_NO_PARTITION = "no-partition"

COLUMN_DATA = "data"
COLUMN_CALCULATED = "calculated"
COLUMN_CALCULATED_TABLE = "calculatedTableColumn"

REF_RESOLVED = "resolved"
REF_AMBIGUOUS = "ambiguous"
REF_UNRESOLVED = "unresolved"
REF_LOCAL = "local"

RELATIONSHIP_DEFAULTS = {
    "isActive": "true",
    "crossFilteringBehavior": "oneDirection",
    "securityFilteringBehavior": "oneDirection",
    "fromCardinality": "many",
    "toCardinality": "one",
    "joinOnDateBehavior": "dateAndTime",
    "relyOnReferentialIntegrity": "false",
}


@dataclass(frozen=True, order=True)
class ObjectRef:
    """Identity of a model object used as a dependency-graph node.

    ``kind`` is one of ``table``, ``column``, ``measure``, ``partition`` or
    ``expression``; ``name`` is empty for table references.
    """

    kind: str
    table: str
    name: str = ""

    @property
    def label(self) -> str:
        if self.kind == "table":
            return f"'{self.table}'"
        if self.kind == "expression":
            return self.name
        if self.kind == "partition":
            return f"'{self.table}' partition {self.name}"
        return f"'{self.table}'[{self.name}]"


@dataclass
class Annotation:
    name: str
    value: str


@dataclass
class Variation:
    name: str
    relationship: str | None
    default_hierarchy: str | None
    is_default: bool


@dataclass
class Column:
    name: str
    table: str
    kind: str
    data_type: str | None = None
    source_column: str | None = None
    expression: str | None = None
    is_hidden: bool = False
    format_string: str | None = None
    description: str | None = None
    display_folder: str | None = None
    summarize_by: str | None = None
    sort_by_column: str | None = None
    data_category: str | None = None
    lineage_tag: str | None = None
    properties: dict[str, str] = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)
    annotations: list[Annotation] = field(default_factory=list)
    variations: list[Variation] = field(default_factory=list)
    references: list[DaxReference] = field(default_factory=list)

    @property
    def ref(self) -> ObjectRef:
        return ObjectRef("column", self.table, self.name)


@dataclass
class Measure:
    name: str
    table: str
    expression: str
    format_string: str | None = None
    format_string_expression: str | None = None
    description: str | None = None
    display_folder: str | None = None
    is_hidden: bool = False
    lineage_tag: str | None = None
    properties: dict[str, str] = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)
    annotations: list[Annotation] = field(default_factory=list)
    references: list[DaxReference] = field(default_factory=list)

    @property
    def ref(self) -> ObjectRef:
        return ObjectRef("measure", self.table, self.name)


@dataclass
class HierarchyLevel:
    name: str
    column: str | None


@dataclass
class Hierarchy:
    name: str
    table: str
    levels: list[HierarchyLevel] = field(default_factory=list)
    is_hidden: bool = False
    description: str | None = None
    annotations: list[Annotation] = field(default_factory=list)


@dataclass
class Partition:
    name: str
    table: str
    source_type: str  # "m", "calculated", "entity", ...
    expression: str | None
    mode: str | None = None
    query_group: str | None = None
    description: str | None = None
    properties: dict[str, str] = field(default_factory=dict)
    annotations: list[Annotation] = field(default_factory=list)
    references: list[DaxReference] = field(default_factory=list)
    query_references: list[str] = field(default_factory=list)

    @property
    def ref(self) -> ObjectRef:
        return ObjectRef("partition", self.table, self.name)

    @property
    def language(self) -> str | None:
        if self.source_type == "m":
            return "m"
        if self.source_type == "calculated":
            return "dax"
        return None


@dataclass
class Table:
    name: str
    file_path: str
    doc_filename: str = ""
    classification: str = TABLE_NO_PARTITION
    description: str | None = None
    is_hidden: bool = False
    is_private: bool = False
    lineage_tag: str | None = None
    properties: dict[str, str] = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)
    annotations: list[Annotation] = field(default_factory=list)
    columns: list[Column] = field(default_factory=list)
    measures: list[Measure] = field(default_factory=list)
    partitions: list[Partition] = field(default_factory=list)
    hierarchies: list[Hierarchy] = field(default_factory=list)

    @property
    def ref(self) -> ObjectRef:
        return ObjectRef("table", self.name)

    @property
    def is_auto_date(self) -> bool:
        return self.classification == TABLE_AUTO_DATE

    def column(self, name: str) -> Column | None:
        key = name.casefold()
        return next((c for c in self.columns if c.name.casefold() == key), None)

    def measure(self, name: str) -> Measure | None:
        key = name.casefold()
        return next((m for m in self.measures if m.name.casefold() == key), None)


@dataclass
class Relationship:
    name: str
    from_table: str
    from_column: str
    to_table: str
    to_column: str
    properties: dict[str, str] = field(default_factory=dict)
    annotations: list[Annotation] = field(default_factory=list)
    valid: bool = True

    def option(self, key: str) -> str | None:
        """Explicit value, falling back to the TMDL default for known options."""
        return self.properties.get(key, RELATIONSHIP_DEFAULTS.get(key))

    @property
    def is_active(self) -> bool:
        return (self.option("isActive") or "true").lower() == "true"

    @property
    def cross_filtering_behavior(self) -> str:
        return self.option("crossFilteringBehavior") or "oneDirection"

    @property
    def is_bidirectional(self) -> bool:
        return self.cross_filtering_behavior == "bothDirections"

    @property
    def from_cardinality(self) -> str:
        return self.option("fromCardinality") or "many"

    @property
    def to_cardinality(self) -> str:
        return self.option("toCardinality") or "one"

    @property
    def join_on_date_behavior(self) -> str | None:
        return self.option("joinOnDateBehavior")

    @property
    def extra_options(self) -> dict[str, str]:
        """Explicit options other than the endpoints and well-known settings."""
        known = set(RELATIONSHIP_DEFAULTS) | {"fromColumn", "toColumn"}
        return {k: v for k, v in self.properties.items() if k not in known}


@dataclass
class DataSource:
    """A physical source discovered in a Power Query (M) partition or expression."""

    connector: str
    table: str
    partition: str
    step: str
    arguments: list[str] = field(default_factory=list)
    navigation: list[dict[str, str]] = field(default_factory=list)
    details: dict[str, str] = field(default_factory=dict)
    format: str | None = None
    is_generic: bool = False

    @property
    def identity(self) -> tuple:
        """Key used to deduplicate sources consumed by several partitions."""
        return (
            self.connector,
            tuple(self.arguments),
            tuple(tuple(sorted(n.items())) for n in self.navigation),
            self.format or "",
        )


@dataclass
class DaxReference:
    raw: str
    kind: str  # "measure" | "column" | "table" | "unknown"
    status: str
    table: str | None = None
    name: str | None = None
    candidates: list[ObjectRef] = field(default_factory=list)
    note: str | None = None

    @property
    def target(self) -> ObjectRef | None:
        if self.status != REF_RESOLVED or self.table is None:
            return None
        if self.kind == "table":
            return ObjectRef("table", self.table)
        return ObjectRef(self.kind, self.table, self.name or "")


@dataclass(frozen=True, order=True)
class Dependency:
    source: ObjectRef
    target: ObjectRef
    via: str  # "dax" | "m"


@dataclass
class SharedExpression:
    name: str
    expression: str
    kind: str | None = None
    description: str | None = None
    annotations: list[Annotation] = field(default_factory=list)
    query_references: list[str] = field(default_factory=list)


@dataclass
class Diagnostic:
    severity: str  # "warning" | "info"
    message: str
    location: str | None = None


@dataclass
class SemanticModel:
    name: str
    model_dir: str
    properties: dict[str, str] = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)
    annotations: list[Annotation] = field(default_factory=list)
    database_properties: dict[str, str] = field(default_factory=dict)
    referenced_tables: list[str] = field(default_factory=list)
    cultures: list[str] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    relationships: list[Relationship] = field(default_factory=list)
    expressions: list[SharedExpression] = field(default_factory=list)
    sources: list[DataSource] = field(default_factory=list)
    dependencies: list[Dependency] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)

    def table(self, name: str) -> Table | None:
        key = name.casefold()
        return next((t for t in self.tables if t.name.casefold() == key), None)

    @property
    def authored_tables(self) -> list[Table]:
        return [t for t in self.tables if not t.is_auto_date]

    @property
    def auto_date_tables(self) -> list[Table]:
        return [t for t in self.tables if t.is_auto_date]

    @property
    def measures(self) -> list[Measure]:
        return [m for t in self.tables for m in t.measures]

    def relationships_for(self, table: str) -> tuple[list[Relationship], list[Relationship]]:
        """Return (outbound, inbound) relationships; outbound means ``from`` side."""
        key = table.casefold()
        outbound = [r for r in self.relationships if r.from_table.casefold() == key]
        inbound = [r for r in self.relationships if r.to_table.casefold() == key]
        return outbound, inbound

    def dependencies_of(self, ref: ObjectRef) -> list[Dependency]:
        return [d for d in self.dependencies if d.source == ref]

    def dependents_of(self, ref: ObjectRef) -> list[Dependency]:
        return [d for d in self.dependencies if d.target == ref]

    def sources_for(self, table: str) -> list[DataSource]:
        key = table.casefold()
        return [s for s in self.sources if s.table.casefold() == key]

    def unique_sources(self) -> list[tuple[DataSource, list[DataSource]]]:
        """Deduplicated physical sources, each with every consuming record."""
        groups: dict[tuple, list[DataSource]] = {}
        for source in self.sources:
            groups.setdefault(source.identity, []).append(source)
        return sorted(
            ((records[0], records) for records in groups.values()),
            key=lambda item: (item[0].connector, item[0].identity),
        )

    def query_order(self) -> list[str]:
        for annotation in self.annotations:
            if annotation.name == "PBI_QueryOrder":
                try:
                    value = json.loads(annotation.value)
                except ValueError:
                    return []
                return [str(v) for v in value] if isinstance(value, list) else []
        return []
