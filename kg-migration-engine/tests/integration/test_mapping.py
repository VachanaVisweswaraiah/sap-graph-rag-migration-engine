"""Integration tests for agents/mapping.py's compute_mapping_coverage() against a
real FalkorDB. Nodes/edges seeded directly via raw Cypher (same pattern as
test_disposition.py) — not the shared fixture, to keep this test self-contained
and avoid coupling to Phase 1's exact-count assertions.
"""

from __future__ import annotations

import pytest
from falkordb import FalkorDB, Graph

from kgme.agents.mapping import compute_mapping_coverage
from kgme.config import Settings
from kgme.db.driver import get_graph

pytestmark = pytest.mark.integration


def _seed_transaction(graph: Graph, node_id: str, module: str) -> None:
    graph.query(
        "MERGE (n:Entity:Transaction {node_id: $node_id}) "
        "SET n.module = $module, n.confidence = 'documented', "
        "n.source_doc = 'TEST_FIXTURE', n.source_ref = 'test'",
        {"node_id": node_id, "module": module},
    )


def _seed_migrates_to(graph: Graph, source_id: str, target_id: str) -> None:
    graph.query(
        "MATCH (s:Entity {node_id: $source_id}) "
        "MERGE (t:Entity {node_id: $target_id}) "
        "MERGE (s)-[r:MIGRATES_TO]->(t) "
        "SET r.confidence = 'inferred', r.source_doc = 'TEST_FIXTURE'",
        {"source_id": source_id, "target_id": target_id},
    )


def test_compute_mapping_coverage_reports_partial_module_honestly(
    client: FalkorDB, settings: Settings
) -> None:
    graph = get_graph(client, settings=settings)
    _seed_transaction(graph, "TX:FIXMAP1", "FIXMODA")
    _seed_transaction(graph, "TX:FIXMAP2", "FIXMODA")
    _seed_migrates_to(graph, "TX:FIXMAP1", "TX:FIXMAPTGT")

    coverage = compute_mapping_coverage(graph, module="FIXMODA")

    assert len(coverage) == 1
    result = coverage[0]
    assert result.total_transactions == 2
    assert result.mapped_transactions == 1
    assert result.mapped_pairs == [("TX:FIXMAP1", "TX:FIXMAPTGT")]


def test_compute_mapping_coverage_reports_zero_mapped_module_honestly(
    client: FalkorDB, settings: Settings
) -> None:
    graph = get_graph(client, settings=settings)
    _seed_transaction(graph, "TX:FIXMAP3", "FIXMODB")
    _seed_transaction(graph, "TX:FIXMAP4", "FIXMODB")

    coverage = compute_mapping_coverage(graph, module="FIXMODB")

    assert len(coverage) == 1
    result = coverage[0]
    assert result.total_transactions == 2
    assert result.mapped_transactions == 0
    assert result.mapped_pairs == []
