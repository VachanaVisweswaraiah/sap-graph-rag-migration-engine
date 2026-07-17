"""Validates node_type and relation values before they are used as literal Cypher
labels/relationship types (FalkorDB, like Neo4j, cannot parameterize a label or
relationship type — see db/loader.py's grouped-query design).

node_type is checked against a closed enum parsed from kg_data_dictionary.csv: that
enum is complete (24 listed, 24 found in the real data). relation is checked against
a UPPER_SNAKE_CASE shape rule instead of a closed enum, because the dictionary's
relation enum is truncated ("...") and lists only 23 of the 47 relation types that
actually appear in kg_edges.csv — treating it as closed would wrongly reject
legitimate data. A relation that is shape-valid but absent from the dictionary's
partial list is logged as an "undocumented enum value" (visible in the audit trail),
not rejected — that's a documentation gap, not a data error.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import structlog

from kgme.core.exceptions import SchemaViolationError
from kgme.core.observability import get_logger

RELATION_SHAPE = re.compile(r"^[A-Z][A-Z0-9_]*$")

# The confidence model (CLAUDE.md's non-negotiable #1): nodes are never `inferred`,
# edges are never `gap`. Enforced here, at ingestion, not just read back later by
# db/health.py -- a blank CSV cell becomes "" (see loader.py's row.get(field, "")),
# which is deliberately NOT in either set, so a blank confidence cell is rejected
# here rather than silently passing an `IS NULL`-only health check downstream.
NODE_CONFIDENCE_VALUES = frozenset({"documented", "peripheral", "gap"})
EDGE_CONFIDENCE_VALUES = frozenset({"documented", "peripheral", "inferred"})


@dataclass(frozen=True)
class DataDictionary:
    allowed_node_types: frozenset[str]
    documented_relations: frozenset[str]


def _split_enum(raw: str) -> set[str]:
    return {part.strip() for part in raw.split("|") if part.strip() and part.strip() != "..."}


def load_data_dictionary(path: Path) -> DataDictionary:
    """Parses data/raw/kg_data_dictionary.csv (utf-8-sig) for the node_type and
    relation enum rows."""
    node_types: set[str] = set()
    relations: set[str] = set()
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row["file"] == "kg_nodes.csv" and row["column"] == "node_type":
                node_types = _split_enum(row["allowed_values_or_format"])
            if row["file"] == "kg_edges.csv" and row["column"] == "relation":
                relations = _split_enum(row["allowed_values_or_format"])
    return DataDictionary(
        allowed_node_types=frozenset(node_types), documented_relations=frozenset(relations)
    )


def validate_rows_against_dictionary(
    nodes: Sequence[Mapping[str, str]],
    edges: Sequence[Mapping[str, str]],
    dictionary: DataDictionary,
    *,
    logger: structlog.stdlib.BoundLogger | None = None,
) -> None:
    """Raises SchemaViolationError, fail-fast and before any write, if any row uses
    a node_type outside the dictionary's closed enum, a relation that isn't
    UPPER_SNAKE_CASE shaped, or a confidence value outside the model's closed enum
    for that row kind (nodes: documented/peripheral/gap; edges: documented/
    peripheral/inferred -- see CLAUDE.md's non-negotiable #1). Logs (does not raise
    for) shape-valid relations that are simply undocumented in the dictionary's
    partial list."""
    logger = logger or get_logger("db.schema")

    bad_node_types = sorted(
        {row["node_type"] for row in nodes if row["node_type"] not in dictionary.allowed_node_types}
    )
    bad_relations = sorted(
        {row["relation"] for row in edges if not RELATION_SHAPE.match(row["relation"])}
    )
    bad_node_confidences = sorted(
        {
            row.get("confidence", "")
            for row in nodes
            if row.get("confidence", "") not in NODE_CONFIDENCE_VALUES
        }
    )
    bad_edge_confidences = sorted(
        {
            row.get("confidence", "")
            for row in edges
            if row.get("confidence", "") not in EDGE_CONFIDENCE_VALUES
        }
    )
    if bad_node_types or bad_relations or bad_node_confidences or bad_edge_confidences:
        raise SchemaViolationError(
            f"unlisted node_type(s): {bad_node_types}; malformed relation(s): {bad_relations}; "
            f"invalid node confidence(s): {bad_node_confidences}; "
            f"invalid edge confidence(s): {bad_edge_confidences}"
        )

    undocumented = sorted({row["relation"] for row in edges} - dictionary.documented_relations)
    if undocumented:
        logger.warning(
            "schema.relation.undocumented",
            relations=undocumented,
            detail=(
                "present in kg_edges.csv, shape-valid, but not listed in "
                "kg_data_dictionary.csv's (partial) relation enum"
            ),
        )
