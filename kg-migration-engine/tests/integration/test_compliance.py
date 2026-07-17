"""NOTE: This file has been sanitized for public/private portfolio use.
Business logic, domain-specific rules, and proprietary details have been masked.
The coding patterns, architecture, and technical implementation remain authentic.
[MASKED] tags indicate where original business logic has been replaced.

Integration tests for agents/compliance.py's run_compliance_scan() against a
real FalkorDB. Nodes/edges seeded directly via raw Cypher (same pattern as
test_disposition.py / test_mapping.py) — self-contained, not the shared fixture.
"""

from __future__ import annotations

import pytest
from falkordb import FalkorDB, Graph

from kgme.agents.compliance import run_compliance_scan, run_s4_catalog_scan
from kgme.config import Settings
from kgme.db.driver import get_graph

pytestmark = pytest.mark.integration


def _seed_node(
    graph: Graph, node_id: str, *, confidence: str = "documented", gxp: str = ""
) -> None:
    graph.query(
        "MERGE (n:Entity {node_id: $node_id}) "
        "SET n.confidence = $confidence, n.gxp_classification = $gxp, "
        "n.source_doc = 'TEST_FIXTURE', n.source_ref = 'test'",
        {"node_id": node_id, "confidence": confidence, "gxp": gxp},
    )


def _seed_edge(
    graph: Graph, source_id: str, relation: str, target_id: str, *, confidence: str
) -> None:
    graph.query(
        f"MATCH (s:Entity {{node_id: $source_id}}), (t:Entity {{node_id: $target_id}}) "
        f"MERGE (s)-[r:{relation}]->(t) "
        "SET r.confidence = $confidence, r.source_doc = 'TEST_FIXTURE'",
        {"source_id": source_id, "target_id": target_id, "confidence": confidence},
    )


def test_run_compliance_scan_finds_gap_node_inferred_edge_and_gxp_target(
    client: FalkorDB, settings: Settings
) -> None:
    graph = get_graph(client, settings=settings)
    _seed_node(graph, "DOC:FIXGAP1", confidence="gap")
    _seed_node(graph, "DOC:FIXGAP2")
    _seed_edge(graph, "DOC:FIXGAP1", "PRECEDES", "DOC:FIXGAP2", confidence="documented")

    _seed_node(graph, "TX:FIXINF1")
    _seed_node(graph, "TX:FIXINF2")
    _seed_edge(graph, "TX:FIXINF1", "SUSPECTED_USES_BWA", "TX:FIXINF2", confidence="inferred")

    _seed_node(graph, "ROLE:FIXROLE")
    _seed_node(graph, "PROC:FIXCRIT", gxp="GxP-kritisch")
    _seed_edge(graph, "ROLE:FIXROLE", "AUTHORIZES", "PROC:FIXCRIT", confidence="documented")

    findings = run_compliance_scan(graph)
    ids = {(f.source_id, f.relation, f.target_id) for f in findings}

    assert ("DOC:FIXGAP1", "PRECEDES", "DOC:FIXGAP2") in ids
    assert ("TX:FIXINF1", "SUSPECTED_USES_BWA", "TX:FIXINF2") in ids
    assert ("ROLE:FIXROLE", "AUTHORIZES", "PROC:FIXCRIT") in ids


def test_run_compliance_scan_sorts_flagship_first_regardless_of_insertion_order(
    client: FalkorDB, settings: Settings
) -> None:
    graph = get_graph(client, settings=settings)
    # Unrelated inferred-edge finding, seeded before the flagship.
    _seed_node(graph, "TX:FIXOTHER1")
    _seed_node(graph, "TX:FIXOTHER2")
    _seed_edge(graph, "TX:FIXOTHER1", "SUSPECTED_USES_BWA", "TX:FIXOTHER2", confidence="inferred")

    _seed_node(graph, "QM:BATCH_RELEASE")
    _seed_node(graph, "SYS:LAB_SYSTEM")
    _seed_edge(
        graph,
        "QM:BATCH_RELEASE",
        "SUSPECTED_SOURCE",
        "SYS:LAB_SYSTEM",
        confidence="inferred",
    )

    findings = run_compliance_scan(graph)
    flagship_matches = [f for f in findings if f.is_flagship]

    assert len(flagship_matches) == 1
    assert findings[0].is_flagship is True
    assert findings[0].source_id == "QM:BATCH_RELEASE"


def test_run_s4_catalog_scan_finds_a_node_with_zero_relationships(
    client: FalkorDB, settings: Settings
) -> None:
    """The actual scenario that motivated this being a separate query from
    run_compliance_scan: a node the S/4 catalog flags that has no relationship at
    all (real example in the live graph: TX:AS21) -- a path-based query would
    never match it."""
    graph = get_graph(client, settings=settings)
    graph.query(
        "MERGE (n:Entity:Transaction {node_id: 'TX:FIXS4ISOLATED'}) "
        "SET n.node_type = 'Transaction', n.confidence = 'documented', n.module = 'AM', "
        "n.label = 'Fixture Isolated Transaction', n.source_doc = 'TEST_FIXTURE', "
        "n.s4_status = 'Deprecated', "
        "n.s4_severity = 'Functional Gap (Process will break)', "
        "n.s4_confidence = 'inferred', "
        "n.s4_source_doc = 'DERIVED:SAP_SIMPLIFICATION_LIST'"
    )

    findings = run_s4_catalog_scan(graph)
    ids = {f.node_id for f in findings}

    assert "TX:FIXS4ISOLATED" in ids
