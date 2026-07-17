"""Integration tests for agents/schema_context.py against a real FalkorDB. Seeds
nodes/edges directly via raw Cypher (same self-contained pattern as
test_disposition.py) rather than reusing the shared fixture CSVs.
"""

from __future__ import annotations

import pytest
from falkordb import FalkorDB

from kgme.agents.schema_context import build_schema_context
from kgme.config import Settings
from kgme.db.driver import get_graph

pytestmark = pytest.mark.integration


def test_build_schema_context_reflects_live_graph_contents(
    client: FalkorDB, settings: Settings
) -> None:
    graph = get_graph(client, settings=settings)
    graph.query(
        "MERGE (a:Entity:Transaction {node_id: 'TX:FIXA'}) "
        "SET a.node_type = 'Transaction', a.confidence = 'documented', "
        "a.gxp_classification = 'GxP-kritisch', a.module = 'MM'"
    )
    graph.query(
        "MERGE (b:Entity:Transaction {node_id: 'TX:FIXB'}) "
        "SET b.node_type = 'Transaction', b.confidence = 'peripheral', "
        "b.gxp_classification = 'unkritisch', b.module = 'AM'"
    )
    graph.query(
        "MATCH (a:Entity {node_id: 'TX:FIXA'}), (b:Entity {node_id: 'TX:FIXB'}) "
        "MERGE (a)-[r:MIGRATES_TO]->(b) SET r.confidence = 'inferred'"
    )

    context = build_schema_context(graph)

    assert "Transaction" in context
    assert "MIGRATES_TO" in context
    assert "documented" in context
    assert "peripheral" in context
    assert "inferred" in context
    assert "GxP-kritisch" in context
    assert "MM" in context
    assert "AM" in context
    # The GxP rules must be present as explicit text, not just implied by the enums.
    assert "NEVER 'inferred'" in context
    assert "NEVER 'gap'" in context
    # The node_id prefix convention must be demonstrated with a real example, not
    # just described abstractly — a real gap found during manual verification.
    assert "TX:FIXA" in context or "TX:FIXB" in context
    assert "NODE_ID CONVENTION" in context
    assert "there is NO `name` property, use `label`" in context


def test_build_schema_context_surfaces_enrichment_properties(
    client: FalkorDB, settings: Settings
) -> None:
    """Regression test: enrichment scripts (disposition.py, s4_simplification.py)
    add namespaced properties that a hardcoded NODE PROPERTIES list would never
    pick up. Found live: the As-Is agent answered "no such data" for a node that
    genuinely had s4_status/s4_severity set, because the schema context never told
    it those properties exist."""
    graph = get_graph(client, settings=settings)
    graph.query(
        "MERGE (a:Entity:Transaction {node_id: 'TX:FIXS4'}) "
        "SET a.node_type = 'Transaction', a.confidence = 'documented', a.module = 'MM', "
        "a.s4_status = 'Deprecated', a.s4_severity = 'Functional Gap (Process will break)', "
        "a.s4_confidence = 'inferred', a.s4_source_doc = 'DERIVED:SAP_SIMPLIFICATION_LIST'"
    )

    context = build_schema_context(graph)

    assert "ADDITIONAL PROPERTIES" in context
    assert "s4_status" in context
    assert "s4_severity" in context
    assert "s4_confidence" in context
    # Baseline properties must never be re-listed as if they were enrichment-only.
    additional_properties_line = next(
        line for line in context.splitlines() if "ADDITIONAL PROPERTIES" in line
    )
    assert "node_id" not in additional_properties_line
    assert "confidence`, `source_doc`" not in additional_properties_line


def test_build_schema_context_omits_the_section_when_no_enrichment_has_run(
    client: FalkorDB, settings: Settings
) -> None:
    graph = get_graph(client, settings=settings)
    graph.query(
        "MERGE (a:Entity:Transaction {node_id: 'TX:FIXPLAIN'}) "
        "SET a.node_type = 'Transaction', a.confidence = 'documented', a.module = 'MM'"
    )

    context = build_schema_context(graph)

    assert "ADDITIONAL PROPERTIES" not in context


def test_build_schema_context_excludes_blank_values(client: FalkorDB, settings: Settings) -> None:
    graph = get_graph(client, settings=settings)
    graph.query(
        "MERGE (a:Entity:Transaction {node_id: 'TX:FIXBLANK'}) "
        "SET a.node_type = 'Transaction', a.confidence = 'documented', "
        "a.gxp_classification = '', a.module = 'MM'"
    )
    graph.query(
        "MERGE (b:Entity:Transaction {node_id: 'TX:FIXREAL'}) "
        "SET b.node_type = 'Transaction', b.confidence = 'documented', "
        "b.gxp_classification = 'unbekannt', b.module = 'MM'"
    )

    context = build_schema_context(graph)

    # The blank value must not leave a dangling artifact (e.g. ", ," or a leading
    # comma) alongside the one real value.
    lines = [line for line in context.splitlines() if "gxp_classification" in line]
    assert lines
    assert ", ," not in lines[0]
    assert "unbekannt" in lines[0]
    assert lines[0].rstrip().endswith("unbekannt")
