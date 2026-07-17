"""NOTE: This file has been sanitized for public/private portfolio use.
Business logic, domain-specific rules, and proprietary details have been masked.
The coding patterns, architecture, and technical implementation remain authentic.
[MASKED] tags indicate where original business logic has been replaced.

Integration tests for db/gaps.py against a real FalkorDB. Nodes/edges seeded
directly via raw Cypher, self-contained (same pattern as test_mapping.py/
test_compliance.py)."""

from __future__ import annotations

import pytest
from falkordb import FalkorDB

from kgme.config import Settings
from kgme.db.driver import get_graph
from kgme.db.gaps import list_gap_nodes, list_inferred_edges

pytestmark = pytest.mark.integration


def test_list_gap_nodes_finds_real_seeded_gap(client: FalkorDB, settings: Settings) -> None:
    graph = get_graph(client, settings=settings)
    graph.query(
        "MERGE (n:Entity {node_id: 'SOP:FIXGAP1'}) "
        "SET n.confidence = 'gap', n.node_type = 'SOP', n.module = 'governance', "
        "n.source_doc = 'TEST_FIXTURE'"
    )
    graph.query(
        "MERGE (n:Entity {node_id: 'SOP:FIXDOCUMENTED'}) "
        "SET n.confidence = 'documented', n.node_type = 'SOP', n.module = 'governance', "
        "n.source_doc = 'TEST_FIXTURE'"
    )

    nodes = list_gap_nodes(graph)
    ids = {n.node_id for n in nodes}

    assert "SOP:FIXGAP1" in ids
    assert "SOP:FIXDOCUMENTED" not in ids


def test_list_inferred_edges_finds_real_seeded_edge(client: FalkorDB, settings: Settings) -> None:
    graph = get_graph(client, settings=settings)
    graph.query(
        "MERGE (s:Entity {node_id: 'TX:FIXINFSRC'}) "
        "MERGE (t:Entity {node_id: 'TX:FIXINFTGT'}) "
        "MERGE (s)-[r:SUSPECTED_USES_BWA]->(t) "
        "SET r.confidence = 'inferred', r.source_doc = 'TEST_FIXTURE'"
    )
    graph.query(
        "MERGE (s:Entity {node_id: 'TX:FIXDOCSRC'}) "
        "MERGE (t:Entity {node_id: 'TX:FIXDOCTGT'}) "
        "MERGE (s)-[r:PRECEDES]->(t) "
        "SET r.confidence = 'documented', r.source_doc = 'TEST_FIXTURE'"
    )

    edges = list_inferred_edges(graph)
    pairs = {(e.source_id, e.relation, e.target_id) for e in edges}

    assert ("TX:FIXINFSRC", "SUSPECTED_USES_BWA", "TX:FIXINFTGT") in pairs
    assert ("TX:FIXDOCSRC", "PRECEDES", "TX:FIXDOCTGT") not in pairs
