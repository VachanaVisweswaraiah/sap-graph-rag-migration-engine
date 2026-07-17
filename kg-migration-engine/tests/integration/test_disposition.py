"""Integration tests for enrichment/disposition.py's apply_dispositions() against a
real FalkorDB. Deliberately does NOT reuse tests/fixtures/kg_nodes_fixture.csv (that
would risk breaking Phase 1's exact-count assertions in test_loader.py) — nodes are
seeded directly via a raw Cypher MERGE inside each test.
"""

from __future__ import annotations

import pytest
from falkordb import FalkorDB, Graph

from kgme.config import Settings
from kgme.core.observability import get_logger
from kgme.db.driver import get_graph
from kgme.enrichment.disposition import DispositionFact, apply_dispositions

pytestmark = pytest.mark.integration


def _seed_node(graph: Graph, node_id: str, node_type: str = "Transaction") -> None:
    graph.query(
        "MERGE (n:Entity {node_id: $node_id}) "
        "SET n.node_type = $node_type, n.confidence = 'documented', "
        "n.source_doc = 'TEST_FIXTURE', n.source_ref = 'test'",
        {"node_id": node_id, "node_type": node_type},
    )


def test_apply_dispositions_creates_migrates_to_edge(client: FalkorDB, settings: Settings) -> None:
    graph = get_graph(client, settings=settings)
    _seed_node(graph, "TX:FIXSRC")
    _seed_node(graph, "TX:FIXTGT")
    fact = DispositionFact(
        kind="migrates_to",
        source_node_id="TX:FIXSRC",
        target_node_id="TX:FIXTGT",
        status=None,
        source_ref="kg_nodes.csv:TX:FIXSRC.notes",
        raw_text="S/4: abgeschaltet -> FIXTGT",
    )

    summary = apply_dispositions(graph, [fact], get_logger("test"))

    assert summary.edges_written == 1
    row = graph.query(
        "MATCH (:Entity {node_id:'TX:FIXSRC'})-[r:MIGRATES_TO]->(:Entity {node_id:'TX:FIXTGT'}) "
        "RETURN r.confidence, r.source_doc, r.source_ref"
    ).result_set[0]
    assert row[0] == "inferred"
    assert row[1] == "DERIVED:s4_disposition"
    assert row[2] == "kg_nodes.csv:TX:FIXSRC.notes"


def test_apply_dispositions_is_idempotent(client: FalkorDB, settings: Settings) -> None:
    graph = get_graph(client, settings=settings)
    _seed_node(graph, "TX:FIXSRC")
    _seed_node(graph, "TX:FIXTGT")
    fact = DispositionFact(
        kind="migrates_to",
        source_node_id="TX:FIXSRC",
        target_node_id="TX:FIXTGT",
        status=None,
        source_ref="ref",
        raw_text="text",
    )

    apply_dispositions(graph, [fact], get_logger("test"))
    apply_dispositions(graph, [fact], get_logger("test"))

    count = graph.query(
        "MATCH (:Entity {node_id:'TX:FIXSRC'})-[r:MIGRATES_TO]->(:Entity {node_id:'TX:FIXTGT'}) "
        "RETURN count(r)"
    ).result_set[0][0]
    assert count == 1


def test_apply_dispositions_reports_unmatched_target_without_creating_node(
    client: FalkorDB, settings: Settings
) -> None:
    graph = get_graph(client, settings=settings)
    _seed_node(graph, "TX:FIXSRC")
    fact = DispositionFact(
        kind="migrates_to",
        source_node_id="TX:FIXSRC",
        target_node_id="TX:DOESNOTEXIST",
        status=None,
        source_ref="ref",
        raw_text="text",
    )

    summary = apply_dispositions(graph, [fact], get_logger("test"))

    assert summary.edges_written == 0
    assert len(summary.unmatched_targets) == 1
    count = graph.query("MATCH (n:Entity {node_id:'TX:DOESNOTEXIST'}) RETURN count(n)").result_set[
        0
    ][0]
    assert count == 0


def test_apply_dispositions_does_not_touch_original_provenance(
    client: FalkorDB, settings: Settings
) -> None:
    graph = get_graph(client, settings=settings)
    _seed_node(graph, "TX:FIXSRC")
    _seed_node(graph, "TX:FIXTGT")
    fact = DispositionFact(
        kind="migrates_to",
        source_node_id="TX:FIXSRC",
        target_node_id="TX:FIXTGT",
        status=None,
        source_ref="ref",
        raw_text="text",
    )

    apply_dispositions(graph, [fact], get_logger("test"))

    row = graph.query(
        "MATCH (n:Entity {node_id:'TX:FIXSRC'}) RETURN n.confidence, n.source_doc, n.source_ref"
    ).result_set[0]
    assert row == ["documented", "TEST_FIXTURE", "test"]


def test_apply_dispositions_status_only_sets_disposition_properties(
    client: FalkorDB, settings: Settings
) -> None:
    graph = get_graph(client, settings=settings)
    _seed_node(graph, "TX:FIXSRC")
    fact = DispositionFact(
        kind="status_only",
        source_node_id="TX:FIXSRC",
        target_node_id=None,
        status="deprecated",
        source_ref="kg_nodes.csv:TX:FIXSRC.notes",
        raw_text="S/4: abgeschaltet",
    )

    summary = apply_dispositions(graph, [fact], get_logger("test"))

    assert summary.properties_written == 1
    row = graph.query(
        "MATCH (n:Entity {node_id:'TX:FIXSRC'}) "
        "RETURN n.disposition_status, n.disposition_confidence, n.disposition_source_doc, "
        "n.confidence, n.source_doc"
    ).result_set[0]
    assert row[0] == "deprecated"
    assert row[1] == "inferred"
    assert row[2] == "DERIVED:s4_disposition"
    # Original provenance and the (separate) s4_simplification namespace are untouched.
    assert row[3] == "documented"
    assert row[4] == "TEST_FIXTURE"
