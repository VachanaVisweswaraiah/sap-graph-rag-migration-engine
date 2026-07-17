"""FalkorDB connection factory and the single chokepoint for running Cypher.

FalkorDB's Python client is stateless per call against a selected named graph — no
session/context-manager model like the neo4j driver. mode=READ routes through
`Graph.ro_query`, which FalkorDB's engine itself rejects write clauses on — a real
DB-level guarantee that Phase 3's cypher_guard and agents/as_is.py will rely on
(backed by a scoped Redis ACL user for defense in depth, per CLAUDE.md).
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from falkordb import FalkorDB, Graph
from falkordb.query_result import QueryResult

from kgme.config import Settings
from kgme.core.exceptions import ConnectionUnavailableError


class AccessMode(StrEnum):
    READ = "READ"
    WRITE = "WRITE"


def build_client(settings: Settings) -> FalkorDB:
    """Build the shared FalkorDB client and verify connectivity eagerly.
    Raises ConnectionUnavailableError with a clear message if unreachable —
    fail fast, per CLAUDE.md."""
    client = FalkorDB(
        host=settings.falkordb_host,
        port=settings.falkordb_port,
        username=settings.falkordb_username,
        password=settings.falkordb_password,
    )
    try:
        client.connection.ping()
    except Exception as exc:
        raise ConnectionUnavailableError(
            f"could not reach FalkorDB at {settings.falkordb_host}:{settings.falkordb_port}: {exc}"
        ) from exc
    return client


def close_client(client: FalkorDB) -> None:
    client.close()


def get_graph(client: FalkorDB, *, settings: Settings) -> Graph:
    """The single chokepoint for obtaining a Graph handle. loader.py and health.py
    both go through this."""
    graph: Graph = client.select_graph(settings.falkordb_graph)
    return graph


def run_query(
    graph: Graph,
    cypher: str,
    params: Mapping[str, Any] | None = None,
    *,
    mode: AccessMode = AccessMode.WRITE,
) -> QueryResult:
    """mode=WRITE -> graph.query(...); mode=READ -> graph.ro_query(...), which
    FalkorDB's engine rejects write clauses on."""
    query_params = dict(params) if params is not None else None
    if mode is AccessMode.READ:
        return graph.ro_query(cypher, query_params)
    return graph.query(cypher, query_params)


def read_only_query(
    graph: Graph, cypher: str, params: Mapping[str, Any] | None = None
) -> QueryResult:
    """run_query(..., mode=AccessMode.READ) under a discoverable name, so a future
    agent import can't accidentally default to WRITE."""
    return run_query(graph, cypher, params, mode=AccessMode.READ)
