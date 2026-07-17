"""NOTE: This file has been sanitized for public/private portfolio use.
Business logic, domain-specific rules, and proprietary details have been masked.
The coding patterns, architecture, and technical implementation remain authentic.
[MASKED] tags indicate where original business logic has been replaced.

Enriches existing ECC nodes with S/4HANA migration-impact facts from the official
SAP Simplification Item Catalog (public SAP Help Portal PDF, extracted to JSON by a
human — not agent-fetched, per CLAUDE.md's deferral of *autonomous* catalog fetching).

Property enrichment, not new nodes/edges: matched nodes get s4_* properties layered on
top of their existing (untouched) confidence/source_doc/source_ref. New properties are
always confidence='inferred' (CLAUDE.md non-negotiable #2 — the match is deterministic
string equality, not an LLM guess, but it's still automated output pending human
review) with source_doc='DERIVED:SAP_SIMPLIFICATION_LIST'.

Matching is deterministic and fails closed: only ecc_object_type values with a known
node_id prefix in this graph (Transaction/Table/Auth_Object) are matched; Program and
Concept rows have no corresponding node type in the graph yet (no ATC scan has run) and
are reported separately, never written. Multi-value/parenthetical/range cells in
ecc_object_name are split conservatively — a range like "AUN1-AUN11" is kept as one
literal token and simply won't match anything, rather than guessing what it expands to.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import structlog
from falkordb import Graph

PREFIX_BY_ECC_OBJECT_TYPE: Final[dict[str, str]] = {
    "Transaction": "TX:",
    "Table": "TAB:",
    "Auth_Object": "AUTH:",
}

_CODE_SHAPE = re.compile(r"^[A-Z][A-Z0-9_]{1,29}$")
_PARENTHETICAL = re.compile(r"\([^)]*\)")


@dataclass(frozen=True)
class CatalogRow:
    simplification_item_id: str
    ecc_object_type: str
    ecc_object_name: str
    s4hana_status: str
    s4hana_target: str
    sap_note_reference: str
    remediation_category: str
    severity: str
    actionable_recommendation: str


@dataclass(frozen=True)
class MatchResult:
    matched: list[tuple[CatalogRow, str]]
    unmatched_codes: list[tuple[CatalogRow, str]]
    skipped_rows: list[CatalogRow]


@dataclass(frozen=True)
class EnrichmentSummary:
    matched_count: int
    unmatched: list[tuple[CatalogRow, str]]
    skipped: list[CatalogRow]


def load_catalog(path: Path) -> list[CatalogRow]:
    """Reads the Simplification Catalog JSON (utf-8-sig, defensive against a BOM
    like every other CSV in this project, even though JSON doesn't require one)."""
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(raw, dict) and "simplification_items" in raw:
        raw = [
            {
                "simplification_item_id": row["sim_id"],
                "ecc_object_type": "Transaction",
                "ecc_object_name": code.removeprefix("TX_"),
                "s4hana_status": "Mandatory" if row.get("mandatory") else "Optional",
                "s4hana_target": row.get("title", ""),
                "sap_note_reference": row["sim_id"],
                "remediation_category": row.get("module", ""),
                "severity": "Mandatory" if row.get("mandatory") else "Optional",
                "actionable_recommendation": row.get("description", ""),
            }
            for row in raw["simplification_items"]
            for code in row.get("impacted_transactions", [])
        ]
    return [
        CatalogRow(
            simplification_item_id=row["simplification_item_id"],
            ecc_object_type=row["ecc_object_type"],
            ecc_object_name=row["ecc_object_name"],
            s4hana_status=row["s4hana_status"],
            s4hana_target=row["s4hana_target"],
            sap_note_reference=row["sap_note_reference"],
            remediation_category=row["remediation_category"],
            severity=row["severity"],
            actionable_recommendation=row["actionable_recommendation"],
        )
        for row in raw
    ]


def extract_codes(ecc_object_name: str) -> list[str]:
    """Splits a (possibly multi-value, parenthetical-annotated) ecc_object_name cell
    into candidate SAP object codes. Only tokens shaped like a real SAP code
    (uppercase start, alnum+underscore) are kept — this naturally filters out
    descriptive words ("BAdI", "user", "exits") without needing a stopword list, and
    a compound range/slash notation ("AUN1-AUN11", "XD01/02/03") simply doesn't match
    the shape and is dropped rather than guessed at."""
    codes: list[str] = []
    seen: set[str] = set()
    for part in re.split(r"[,;]", ecc_object_name):
        cleaned = _PARENTHETICAL.sub("", part)
        for token in cleaned.split():
            if _CODE_SHAPE.match(token) and token not in seen:
                seen.add(token)
                codes.append(token)
    return codes


def match_catalog_to_nodes(
    rows: Sequence[CatalogRow], existing_node_ids: frozenset[str]
) -> MatchResult:
    matched: list[tuple[CatalogRow, str]] = []
    unmatched_codes: list[tuple[CatalogRow, str]] = []
    skipped_rows: list[CatalogRow] = []

    for row in rows:
        prefix = PREFIX_BY_ECC_OBJECT_TYPE.get(row.ecc_object_type)
        if prefix is None:
            skipped_rows.append(row)
            continue
        for code in extract_codes(row.ecc_object_name):
            node_id = f"{prefix}{code}"
            if node_id in existing_node_ids:
                matched.append((row, node_id))
            else:
                unmatched_codes.append((row, code))

    return MatchResult(matched=matched, unmatched_codes=unmatched_codes, skipped_rows=skipped_rows)


def build_enrichment_properties(row: CatalogRow) -> dict[str, str]:
    """Namespaced under s4_* — deliberately does NOT touch the node's own top-level
    confidence/source_doc/source_ref, which describe the original ECC documentation
    (e.g. RA_PROC01) and must not be overwritten by this second, independent
    provenance trail."""
    return {
        "s4_status": row.s4hana_status,
        "s4_target": row.s4hana_target,
        "s4_note": row.sap_note_reference,
        "s4_severity": row.severity,
        "s4_remediation_category": row.remediation_category,
        "s4_confidence": "inferred",
        "s4_source_doc": "DERIVED:SAP_SIMPLIFICATION_LIST",
        "s4_source_ref": f"{row.simplification_item_id} (SAP Note {row.sap_note_reference})",
    }


def enrich_graph(
    graph: Graph, rows: Sequence[CatalogRow], logger: structlog.stdlib.BoundLogger
) -> EnrichmentSummary:
    """1. Load existing node_ids. 2. Match. 3. UNWIND-batched SET (idempotent by
    construction — re-running just re-sets the same values, no MERGE/CREATE
    involved so there's no duplication risk). 4. Log the audit event."""
    existing_node_ids = frozenset(
        r[0] for r in graph.query("MATCH (n:Entity) RETURN n.node_id").result_set
    )
    result = match_catalog_to_nodes(rows, existing_node_ids)

    batch: list[dict[str, Any]] = [
        {"node_id": node_id, "props": build_enrichment_properties(row)}
        for row, node_id in result.matched
    ]
    if batch:
        graph.query(
            "UNWIND $rows AS r MATCH (n:Entity {node_id: r.node_id}) SET n += r.props",
            {"rows": batch},
        )

    logger.info(
        "enrichment.s4_catalog.completed",
        matched_count=len(result.matched),
        unmatched_count=len(result.unmatched_codes),
        skipped_count=len(result.skipped_rows),
    )
    return EnrichmentSummary(
        matched_count=len(result.matched),
        unmatched=result.unmatched_codes,
        skipped=result.skipped_rows,
    )
