"""Generic, reusable health/readiness checks — distinct from cypher/05_verify.cypher
(the loader's own dataset-size-specific self-consistency check, tied to a particular
load's expected node/edge counts). This module has zero web-framework imports, so
Phase 4's FastAPI GET /health route can wrap it directly:
    report = run_health_checks(client, settings=settings)
    return report, (200 if report.healthy else 503)
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from falkordb import FalkorDB

from kgme.config import Settings
from kgme.db.driver import get_graph


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str
    duration_ms: float


@dataclass(frozen=True)
class HealthReport:
    healthy: bool
    checks: list[CheckResult]


def _timed(name: str, fn: Callable[[], str]) -> CheckResult:
    start = time.monotonic()
    try:
        detail = fn()
    except Exception as exc:
        duration_ms = (time.monotonic() - start) * 1000
        return CheckResult(name=name, ok=False, detail=str(exc), duration_ms=duration_ms)
    duration_ms = (time.monotonic() - start) * 1000
    return CheckResult(name=name, ok=True, detail=detail, duration_ms=duration_ms)


def check_connectivity(client: FalkorDB) -> CheckResult:
    """Liveness: a bare Redis ping, cheaper than any graph query."""

    def _fn() -> str:
        client.connection.ping()
        return "reachable"

    return _timed("connectivity", _fn)


def check_graph_selected(client: FalkorDB, *, settings: Settings) -> CheckResult:
    """Readiness: the configured graph responds to a trivial query."""

    def _fn() -> str:
        graph = get_graph(client, settings=settings)
        graph.query("RETURN 1")
        return f"graph {settings.falkordb_graph!r} responds"

    return _timed("graph_selected", _fn)


def check_constraints(client: FalkorDB, *, settings: Settings) -> CheckResult:
    """Readiness: the Entity.node_id uniqueness constraint exists and isn't FAILED.
    On a graph that was never written to, list_constraints itself raises — that's
    reported as this check failing (not ready), not as an unrelated crash."""

    def _fn() -> str:
        graph = get_graph(client, settings=settings)
        try:
            constraints = graph.list_constraints()
        except Exception as exc:
            raise RuntimeError(f"no constraints found (graph not loaded yet?): {exc}") from exc
        match = next(
            (
                c
                for c in constraints
                if c["type"] == "UNIQUE" and c["label"] == "Entity" and "node_id" in c["properties"]
            ),
            None,
        )
        if match is None:
            raise RuntimeError("Entity.node_id uniqueness constraint not found")
        if match["status"] == "FAILED":
            raise RuntimeError(f"constraint status is {match['status']!r}")
        return f"constraint status: {match['status']}"

    return _timed("constraints", _fn)


def check_provenance_complete(client: FalkorDB, *, settings: Settings) -> CheckResult:
    """Deep check: no node or edge is missing confidence/source_doc. Independent of
    dataset size, unlike cypher/05_verify.cypher's count assertions."""

    def _fn() -> str:
        graph = get_graph(client, settings=settings)
        bad_nodes = graph.ro_query(
            "MATCH (n:Entity) WHERE n.confidence IS NULL OR n.source_doc IS NULL RETURN count(n)"
        ).result_set[0][0]
        bad_edges = graph.ro_query(
            "MATCH ()-[r]->() WHERE r.confidence IS NULL OR r.source_doc IS NULL RETURN count(r)"
        ).result_set[0][0]
        if bad_nodes or bad_edges:
            raise RuntimeError(
                f"{bad_nodes} node(s) and {bad_edges} edge(s) missing confidence/source_doc"
            )
        return "every node and edge carries confidence + source_doc"

    return _timed("provenance_complete", _fn)


def run_health_checks(client: FalkorDB, *, settings: Settings, deep: bool = False) -> HealthReport:
    """deep=False: connectivity + graph_selected + constraints (cheap, for a future
    per-request HTTP /health). deep=True: also check_provenance_complete — what
    `kgme db check --deep` runs from the CLI."""
    checks = [
        check_connectivity(client),
        check_graph_selected(client, settings=settings),
        check_constraints(client, settings=settings),
    ]
    if deep:
        checks.append(check_provenance_complete(client, settings=settings))
    return HealthReport(healthy=all(c.ok for c in checks), checks=checks)
