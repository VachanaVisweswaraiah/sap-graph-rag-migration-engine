"""Phase 1 loader: reads kg_nodes.csv/kg_edges.csv directly (utf-8-sig), validates them
against kg_data_dictionary.csv (db/schema.py), then loads FalkorDB via a native
constraint call plus UNWIND-batched parameterized MERGE queries, grouped by node_type/
relation since plain Cypher can't parameterize a label or relationship type.

Idempotent: MERGE keyed on node_id / edge_id. Every step aborts the run on failure
except the final verify step, which never raises — it returns boolean assertion
columns instead (see cypher/05_verify.cypher).
"""

from __future__ import annotations

import contextlib
import csv
import os
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from itertools import groupby
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

import structlog
from falkordb import FalkorDB, Graph

from kgme.core.exceptions import KgmeError, LoadAbortedError, SchemaViolationError
from kgme.core.observability import bind_run_id, get_logger
from kgme.db.driver import get_graph
from kgme.db.schema import (
    RELATION_SHAPE,
    DataDictionary,
    load_data_dictionary,
    validate_rows_against_dictionary,
)

if TYPE_CHECKING:
    from kgme.config import Settings

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
VERIFY_CYPHER_PATH: Final[Path] = _REPO_ROOT / "cypher" / "05_verify.cypher"

NODE_PROPERTY_FIELDS: Final[tuple[str, ...]] = (
    "node_type",
    "label",
    "module",
    "gxp_classification",
    "confidence",
    "source_doc",
    "source_ref",
    "notes",
)
EDGE_PROPERTY_FIELDS: Final[tuple[str, ...]] = ("confidence", "source_doc", "source_ref", "notes")

STEP_ORDER: Final[tuple[str, ...]] = (
    "constraints",
    "load_nodes",
    "load_edges",
    "promote_labels",
    "verify",
)
NON_ABORTING_STEPS: Final[frozenset[str]] = frozenset({"verify"})


@dataclass(frozen=True)
class StepResult:
    step: str
    ok: bool
    summary: dict[str, Any]
    duration_ms: float


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    """Reads a CSV file as utf-8-sig (per CLAUDE.md's hard rule — the source files
    have a UTF-8 BOM). No file staging: this reads data/raw/*.csv directly."""
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def wipe_graph(graph: Graph, *, wipe: bool, confirm_env: str = "KGME_ALLOW_WIPE") -> None:
    """DEV/TEST-ONLY. Double-gated: raises unless BOTH wipe=True is passed
    explicitly AND the environment variable KGME_ALLOW_WIPE=1 is set, so a
    copy-pasted --wipe can't be catastrophic."""
    if not wipe:
        return
    if os.environ.get(confirm_env) != "1":
        raise KgmeError(
            f"wipe=True requires the environment variable {confirm_env}=1 to be set explicitly"
        )
    with contextlib.suppress(Exception):
        # Deleting a graph that doesn't exist yet is a no-op.
        graph.delete()


def _run_step(
    step_name: str,
    fn: Callable[[], Mapping[str, Any]],
    logger: structlog.stdlib.BoundLogger,
    *,
    ok_fn: Callable[[Mapping[str, Any]], bool] = lambda _summary: True,
) -> StepResult:
    start = time.monotonic()
    try:
        summary = dict(fn())
    except Exception as exc:
        duration_ms = (time.monotonic() - start) * 1000
        logger.error(
            "loader.step.failed",
            step=step_name,
            status="error",
            duration_ms=duration_ms,
            error_type=type(exc).__name__,
            error_detail=str(exc),
        )
        raise LoadAbortedError(f"step '{step_name}' failed: {exc}") from exc
    duration_ms = (time.monotonic() - start) * 1000
    ok = ok_fn(summary)
    logger.info(
        "loader.step.completed",
        step=step_name,
        status="ok" if ok else "failed_assertion",
        duration_ms=duration_ms,
        **summary,
    )
    return StepResult(step=step_name, ok=ok, summary=summary, duration_ms=duration_ms)


def _constraints_step(graph: Graph) -> Mapping[str, Any]:
    """Graph.create_node_unique_constraint is NOT idempotent by itself — a second
    call raises "Constraint already exists" rather than no-op'ing. Check first.
    On a brand-new graph (nothing ever written), list_constraints itself raises
    ("Invalid graph operation on empty key") — that means no constraints exist yet."""
    try:
        existing = graph.list_constraints()
    except Exception:
        existing = []
    already_present = any(
        c["type"] == "UNIQUE" and c["label"] == "Entity" and "node_id" in c["properties"]
        for c in existing
    )
    if not already_present:
        graph.create_node_unique_constraint("Entity", "node_id")
    return {"constraint": "Entity.node_id UNIQUE"}


def _load_nodes_step(graph: Graph, nodes: Sequence[Mapping[str, str]]) -> Mapping[str, Any]:
    rows = [
        {
            "node_id": row["node_id"],
            "props": {field: row.get(field, "") for field in NODE_PROPERTY_FIELDS},
        }
        for row in nodes
    ]
    graph.query(
        "UNWIND $rows AS r MERGE (n:Entity {node_id: r.node_id}) SET n += r.props",
        {"rows": rows},
    )
    return {"count": len(rows)}


def _load_edges_step(
    graph: Graph, edges: Sequence[Mapping[str, str]], dictionary: DataDictionary
) -> Mapping[str, Any]:
    total = 0
    per_relation: dict[str, int] = {}
    sorted_edges = sorted(edges, key=lambda r: r["relation"])
    for relation, group in groupby(sorted_edges, key=lambda r: r["relation"]):
        if not RELATION_SHAPE.match(relation):
            # Belt-and-suspenders: validate_rows_against_dictionary already checked this
            # before load_graph ever called this function.
            raise SchemaViolationError(f"refusing to use unvalidated relation type: {relation!r}")
        rows = [
            {
                "source_id": row["source_id"],
                "target_id": row["target_id"],
                "edge_id": row["edge_id"],
                "props": {field: row.get(field, "") for field in EDGE_PROPERTY_FIELDS},
            }
            for row in group
        ]
        # MERGE below keys on (endpoints + edge_id), since Cypher can't re-point an
        # existing relationship's start/end nodes. If a re-load ever corrects a row's
        # source_id/target_id for the same edge_id, MERGE alone can't find the old
        # relationship (its endpoints differ) and would create a second, new one,
        # leaving the old, now-wrong edge orphaned in the graph forever. Delete any
        # same-edge_id relationship whose endpoints no longer match this row *first*,
        # so the MERGE below always converges on exactly one edge per edge_id.
        delete_stale_cypher = (
            "UNWIND $rows AS r "
            f"MATCH (s)-[rel:{relation} {{edge_id: r.edge_id}}]->(t) "
            "WHERE s.node_id <> r.source_id OR t.node_id <> r.target_id "
            "DELETE rel"
        )
        graph.query(delete_stale_cypher, {"rows": rows})

        cypher = (
            "UNWIND $rows AS r "
            "MATCH (s:Entity {node_id: r.source_id}) "
            "MATCH (t:Entity {node_id: r.target_id}) "
            f"MERGE (s)-[rel:{relation} {{edge_id: r.edge_id}}]->(t) "
            "SET rel += r.props"
        )
        graph.query(cypher, {"rows": rows})
        per_relation[relation] = len(rows)
        total += len(rows)
    return {"count": total, "relations": per_relation}


def _promote_labels_step(
    graph: Graph, nodes: Sequence[Mapping[str, str]], dictionary: DataDictionary
) -> Mapping[str, Any]:
    total = 0
    per_type: dict[str, int] = {}
    for node_type, group in groupby(
        sorted(nodes, key=lambda r: r["node_type"]), key=lambda r: r["node_type"]
    ):
        if node_type not in dictionary.allowed_node_types:
            # Belt-and-suspenders: validate_rows_against_dictionary already checked this.
            raise SchemaViolationError(
                f"refusing to use unvalidated node_type as a label: {node_type!r}"
            )
        count = sum(1 for _ in group)
        cypher = f"MATCH (n:Entity {{node_type: $node_type}}) SET n:{node_type}"
        graph.query(cypher, {"node_type": node_type})
        per_type[node_type] = count
        total += count
    return {"count": total, "node_types": per_type}


def _strip_trailing_semicolon(cypher: str) -> str:
    """FalkorDB parses a trailing `;` followed by any trailing whitespace/newline as
    a second (empty) statement and rejects the query with "more than one statement
    is not supported" — even though a bare trailing `;` with nothing after it is
    fine. Stripping is the robust fix regardless of exactly how a .cypher file ends."""
    stripped = cypher.strip()
    return stripped.removesuffix(";")


def _verify_step(graph: Graph, *, expected_nodes: int, expected_edges: int) -> Mapping[str, Any]:
    verify_cypher = _strip_trailing_semicolon(VERIFY_CYPHER_PATH.read_text(encoding="utf-8"))
    result = graph.ro_query(
        verify_cypher, {"expected_nodes": expected_nodes, "expected_edges": expected_edges}
    )
    # Positional unpacking, matching cypher/05_verify.cypher's RETURN clause order exactly.
    nodes, edges, nodes_ok, edges_ok, nodes_missing_provenance, provenance_ok = result.result_set[0]
    return {
        "nodes": nodes,
        "edges": edges,
        "nodes_ok": nodes_ok,
        "edges_ok": edges_ok,
        "nodes_missing_provenance": nodes_missing_provenance,
        "provenance_ok": provenance_ok,
    }


def load_graph(
    client: FalkorDB,
    *,
    settings: Settings,
    nodes_path: Path,
    edges_path: Path,
    data_dictionary_path: Path,
    wipe: bool = False,
) -> list[StepResult]:
    """Runs the full Phase 1 pipeline: read -> validate -> constraints -> load nodes ->
    load edges -> promote labels -> verify. Any step's failure aborts the run
    immediately (raises LoadAbortedError / SchemaViolationError) except the final
    verify step, whose StepResult.ok reflects its assertions rather than raising."""
    logger = get_logger("db.loader")
    logger, run_id = bind_run_id(logger)
    logger.info("loader.run.started", run_id=run_id)

    nodes = read_csv_rows(nodes_path)
    edges = read_csv_rows(edges_path)
    dictionary = load_data_dictionary(data_dictionary_path)
    validate_rows_against_dictionary(nodes, edges, dictionary, logger=logger)

    graph = get_graph(client, settings=settings)
    wipe_graph(graph, wipe=wipe)

    results: list[StepResult] = []
    try:
        results.append(_run_step("constraints", lambda: _constraints_step(graph), logger))
        results.append(_run_step("load_nodes", lambda: _load_nodes_step(graph, nodes), logger))
        results.append(
            _run_step("load_edges", lambda: _load_edges_step(graph, edges, dictionary), logger)
        )
        results.append(
            _run_step(
                "promote_labels", lambda: _promote_labels_step(graph, nodes, dictionary), logger
            )
        )
        results.append(
            _run_step(
                "verify",
                lambda: _verify_step(graph, expected_nodes=len(nodes), expected_edges=len(edges)),
                logger,
                ok_fn=lambda s: bool(s["nodes_ok"] and s["edges_ok"] and s["provenance_ok"]),
            )
        )
    except LoadAbortedError:
        logger.error("loader.run.aborted", run_id=run_id)
        raise

    logger.info("loader.run.completed", run_id=run_id)
    return results
