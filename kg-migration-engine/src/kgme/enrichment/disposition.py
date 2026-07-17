"""NOTE: This file has been sanitized for public/private portfolio use.
Business logic, domain-specific rules, and proprietary details have been masked.
The coding patterns, architecture, and technical implementation remain authentic.
[MASKED] tags indicate where original business logic has been replaced.

Derives S/4HANA migration facts from text NovaPharm Biologics's own hand-curated data already
contains — as opposed to enrichment/s4_simplification.py, which derives facts from an
*external* SAP document.

Two sources, both read-only inputs (kg_process_master.csv is still never graph-loaded
as its own node type per CLAUDE.md — only its s4_disposition text is parsed here):

1. kg_nodes.csv's own `notes` field on Transaction/CustomTransaction nodes, e.g.
   `TX:MB1C notes="S/4: abgeschaltet -> MIGO/Fiori"`.
2. kg_process_master.csv's `s4_disposition` column, e.g.
   `"Zwangsumbau (MB1C->MIGO) + Revalidierung"` or a wildcard form
   `"Zwangsumbau (MB1*->MIGO)"` (resolved against that same row's key_transactions
   column — deterministic, bounded to that row's own data, never a corpus-wide guess).

Per HANDOFF_DETAIL.md §3.3, s4_disposition text is itself "my migration call per
process... opinion, not source" — so every derived fact here is confidence='inferred',
source_doc='DERIVED:s4_disposition', under a disposition_* property namespace kept
fully separate from s4_simplification.py's s4_* namespace (independent provenance
trails; a future agent can cross-check them rather than one silently overwriting the
other).

Fails closed throughout: unparseable text is reported, never guessed at (no
"Zwangsumbau (New Asset Accounting; ...)"-style conceptual note is force-fit into a
transaction-level fact); a migration fact whose source or target node doesn't already
exist in the graph is reported as unmatched, never used to create a new node.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import structlog
from falkordb import Graph

from kgme.db.loader import read_csv_rows

_CODE_SHAPE = re.compile(r"^[A-Z][A-Z0-9_]{1,29}$")
_NOTES_ABGESCHALTET_ARROW = re.compile(r"^abgeschaltet\s*->\s*([A-Z][A-Z0-9_]*)")
_NOTES_ABGESCHALTET_ALONE = re.compile(r"^abgeschaltet\s*$")
_NOTES_ZENTRALE_TA = re.compile(r"^zentrale\s+TA\s*$")
_DISPOSITION_ARROW = re.compile(
    r"Zwangsumbau\s*\(\s*([A-Z][A-Z0-9_]*)(\*)?\s*->\s*([A-Z][A-Z0-9_]*)"
)
_DISPOSITION_BLEIBT = re.compile(r"Zwangsumbau\s*\(\s*([A-Z][A-Z0-9_]*)\s+bleibt\s*\)")


@dataclass(frozen=True)
class DispositionFact:
    kind: Literal["migrates_to", "status_only"]
    source_node_id: str
    target_node_id: str | None
    status: str | None
    source_ref: str
    raw_text: str


@dataclass(frozen=True)
class UnparsedEntry:
    source_ref: str
    raw_text: str


@dataclass(frozen=True)
class DispositionSummary:
    """Reflects only what apply_dispositions itself observes — unparsed rows are a
    parsing-stage concept (see load_dispositions' second return value), not visible
    here since apply_dispositions only ever receives already-parsed facts."""

    edges_written: int
    properties_written: int
    unmatched_targets: list[DispositionFact]


def extract_key_transaction_codes(cell: str) -> list[str]:
    """key_transactions cells use '/' and ';' as separators and are sometimes wrapped
    in one layer of parentheses with a trailing descriptive note (e.g.
    "(MB1A/MB1B/MBRL via Basisrolle)"). This is a different convention from
    enrichment/s4_simplification.py's extract_codes (which splits on ','/';' only and
    deliberately does not split on '/' — changing that would regress already-shipped
    behavior there), so this is a dedicated function, not a reuse."""
    s = cell.strip()
    wrapped = re.match(r"^\((.*)\)$", s)
    if wrapped:
        s = wrapped.group(1)
    codes: list[str] = []
    seen: set[str] = set()
    for part in re.split(r"[;/]", s):
        cleaned = re.sub(r"\([^)]*\)", "", part).strip()
        for token in cleaned.split():
            if _CODE_SHAPE.match(token) and token not in seen:
                seen.add(token)
                codes.append(token)
    return codes


def _node_prefix(node_id: str) -> str:
    return node_id.split(":", 1)[0] + ":" if ":" in node_id else ""


def parse_node_notes(node_id: str, notes: str) -> DispositionFact | UnparsedEntry | None:
    """None if notes doesn't start with 'S/4:' — the notes column serves many purposes
    (spec gaps, GxP remarks, etc.), so only S/4:-prefixed cells are a disposition
    *attempt* at all; everything else is simply out of scope, not a parse failure."""
    stripped = notes.strip()
    if not stripped.startswith("S/4:"):
        return None
    rest = stripped[len("S/4:") :].strip()
    source_ref = f"kg_nodes.csv:{node_id}.notes"
    prefix = _node_prefix(node_id)

    arrow = _NOTES_ABGESCHALTET_ARROW.match(rest)
    if arrow:
        target_node_id = f"{prefix}{arrow.group(1)}"
        return DispositionFact(
            kind="migrates_to",
            source_node_id=node_id,
            target_node_id=target_node_id,
            status=None,
            source_ref=source_ref,
            raw_text=notes,
        )
    if _NOTES_ABGESCHALTET_ALONE.match(rest):
        return DispositionFact(
            kind="status_only",
            source_node_id=node_id,
            target_node_id=None,
            status="deprecated",
            source_ref=source_ref,
            raw_text=notes,
        )
    if _NOTES_ZENTRALE_TA.match(rest):
        return DispositionFact(
            kind="status_only",
            source_node_id=node_id,
            target_node_id=None,
            status="central",
            source_ref=source_ref,
            raw_text=notes,
        )
    return UnparsedEntry(source_ref=source_ref, raw_text=notes)


def parse_process_disposition(
    process_id: str, disposition: str, key_transactions: str
) -> list[DispositionFact] | UnparsedEntry:
    """Every s4_disposition cell is a disposition attempt (unlike kg_nodes.csv's notes
    column, this column's entire purpose is migration disposition) — so every
    non-matching row (including "offen (Doku fehlt)" and "Synergie (...)") is reported
    as UnparsedEntry, not silently skipped."""
    text = disposition.strip()
    source_ref = f"kg_process_master.csv:{process_id}.s4_disposition"
    if not text.startswith("Zwangsumbau"):
        return UnparsedEntry(source_ref=source_ref, raw_text=disposition)

    arrow = _DISPOSITION_ARROW.search(text)
    if arrow:
        source_prefix, is_wildcard, target_code = arrow.group(1), arrow.group(2), arrow.group(3)
        target_node_id = f"TX:{target_code}"
        if is_wildcard:
            candidates = extract_key_transaction_codes(key_transactions)
            matches = [c for c in candidates if c.startswith(source_prefix)]
            if not matches:
                return UnparsedEntry(source_ref=source_ref, raw_text=disposition)
            return [
                DispositionFact(
                    kind="migrates_to",
                    source_node_id=f"TX:{code}",
                    target_node_id=target_node_id,
                    status=None,
                    source_ref=source_ref,
                    raw_text=disposition,
                )
                for code in matches
            ]
        return [
            DispositionFact(
                kind="migrates_to",
                source_node_id=f"TX:{source_prefix}",
                target_node_id=target_node_id,
                status=None,
                source_ref=source_ref,
                raw_text=disposition,
            )
        ]

    bleibt = _DISPOSITION_BLEIBT.search(text)
    if bleibt:
        return [
            DispositionFact(
                kind="status_only",
                source_node_id=f"TX:{bleibt.group(1)}",
                target_node_id=None,
                status="unchanged",
                source_ref=source_ref,
                raw_text=disposition,
            )
        ]

    return UnparsedEntry(source_ref=source_ref, raw_text=disposition)


def load_dispositions(
    nodes_path: Path, process_master_path: Path
) -> tuple[list[DispositionFact], list[UnparsedEntry]]:
    """Reads both CSVs (utf-8-sig via db.loader.read_csv_rows) and runs both parsers
    over every row. Never raises on an unparsed cell — only a structurally missing
    column would raise, which is a real data-shape error, not a parse gap."""
    facts: list[DispositionFact] = []
    unparsed: list[UnparsedEntry] = []

    for row in read_csv_rows(nodes_path):
        if row["node_type"] not in ("Transaction", "CustomTransaction"):
            continue
        result = parse_node_notes(row["node_id"], row["notes"])
        if result is None:
            continue
        if isinstance(result, UnparsedEntry):
            unparsed.append(result)
        else:
            facts.append(result)

    for row in read_csv_rows(process_master_path):
        result_list = parse_process_disposition(
            row["process_id"], row["s4_disposition"], row["key_transactions"]
        )
        if isinstance(result_list, UnparsedEntry):
            unparsed.append(result_list)
        else:
            facts.extend(result_list)

    return facts, unparsed


def apply_dispositions(
    graph: Graph, facts: Sequence[DispositionFact], logger: structlog.stdlib.BoundLogger
) -> DispositionSummary:
    """Idempotent by construction: MIGRATES_TO edges are MERGEd keyed on the
    (source, target, relation) pattern itself (no synthetic edge_id needed — restating
    the same fact from multiple process rows collapses to one edge); status
    properties are a plain SET. Fails closed: a fact whose source or target node_id
    isn't already a real node in the graph is reported in unmatched_targets and never
    creates one."""
    existing_node_ids = frozenset(
        r[0] for r in graph.query("MATCH (n:Entity) RETURN n.node_id").result_set
    )

    migrates: dict[tuple[str, str], DispositionFact] = {}
    statuses: dict[tuple[str, str | None], DispositionFact] = {}
    unmatched: list[DispositionFact] = []

    for fact in facts:
        if fact.kind == "migrates_to":
            assert fact.target_node_id is not None
            source_ok = fact.source_node_id in existing_node_ids
            target_ok = fact.target_node_id in existing_node_ids
            if source_ok and target_ok:
                # The same fact is often restated by multiple process rows (e.g.
                # MB1A->MIGO from MM03/05/07/08/12 alike) — dedupe here so the
                # reported count reflects distinct edges, not raw input rows
                # (MERGE would collapse duplicates in the graph regardless, but a
                # report saying "12 edges written" when only 3 exist is misleading).
                migrates[(fact.source_node_id, fact.target_node_id)] = fact
            else:
                unmatched.append(fact)
        else:
            if fact.source_node_id in existing_node_ids:
                statuses[(fact.source_node_id, fact.status)] = fact
            else:
                unmatched.append(fact)

    if migrates:
        rows: list[dict[str, Any]] = [
            {
                "source_id": f.source_node_id,
                "target_id": f.target_node_id,
                "source_ref": f.source_ref,
                "raw_text": f.raw_text,
            }
            for f in migrates.values()
        ]
        graph.query(
            "UNWIND $rows AS r "
            "MATCH (s:Entity {node_id: r.source_id}) "
            "MATCH (t:Entity {node_id: r.target_id}) "
            "MERGE (s)-[rel:MIGRATES_TO]->(t) "
            "SET rel.confidence = 'inferred', rel.source_doc = 'DERIVED:s4_disposition', "
            "rel.source_ref = r.source_ref, rel.notes = r.raw_text",
            {"rows": rows},
        )

    if statuses:
        rows = [
            {"node_id": f.source_node_id, "status": f.status, "source_ref": f.source_ref}
            for f in statuses.values()
        ]
        graph.query(
            "UNWIND $rows AS r "
            "MATCH (n:Entity {node_id: r.node_id}) "
            "SET n.disposition_status = r.status, n.disposition_confidence = 'inferred', "
            "n.disposition_source_doc = 'DERIVED:s4_disposition', "
            "n.disposition_source_ref = r.source_ref",
            {"rows": rows},
        )

    logger.info(
        "enrichment.disposition.completed",
        edges_written=len(migrates),
        properties_written=len(statuses),
        unmatched_count=len(unmatched),
    )
    return DispositionSummary(
        edges_written=len(migrates),
        properties_written=len(statuses),
        unmatched_targets=unmatched,
    )
