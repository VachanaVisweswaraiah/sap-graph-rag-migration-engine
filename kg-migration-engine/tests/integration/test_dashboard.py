"""Integration tests for the two server-rendered dashboard views against a real
FalkorDB, mocked Anthropic client (app construction needs one, never called by
these routes). TestClient(create_app(...)), same pattern as test_api.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from falkordb import FalkorDB, Graph
from fastapi.testclient import TestClient

from kgme.api.app_factory import create_app
from kgme.config import Settings
from kgme.db.driver import get_graph

pytestmark = pytest.mark.integration


def _seed_transaction(graph: Graph, node_id: str, module: str) -> None:
    graph.query(
        "MERGE (n:Entity:Transaction {node_id: $node_id}) "
        "SET n.module = $module, n.node_type = 'Transaction', n.confidence = 'documented', "
        "n.source_doc = 'TEST_FIXTURE', n.source_ref = 'test', n.label = 'Fixture TX'",
        {"node_id": node_id, "module": module},
    )


def _make_client(graph: Graph, falkordb_client: FalkorDB, settings: Settings) -> TestClient:
    app = create_app(
        client=falkordb_client, graph=graph, anthropic_client=MagicMock(), settings=settings
    )
    return TestClient(app)


def test_module_impact_view_renders_real_module_data(client: FalkorDB, settings: Settings) -> None:
    graph = get_graph(client, settings=settings)
    _seed_transaction(graph, "TX:FIXDASH1", "MM")
    test_client = _make_client(graph, client, settings)

    response = test_client.get("/dashboard/module-impact")

    assert response.status_code == 200
    assert "MM" in response.text
    assert "MIGRATES_TO coverage" in response.text
    assert "none flagged" in response.text  # no s4-catalog enrichment run in this test


def test_module_impact_view_renders_s4_catalog_flags(client: FalkorDB, settings: Settings) -> None:
    graph = get_graph(client, settings=settings)
    _seed_transaction(graph, "TX:FIXDASH2", "MM")
    graph.query(
        "MATCH (n:Entity {node_id: $node_id}) "
        "SET n.s4_status = 'Deprecated', n.s4_severity = $severity",
        {"node_id": "TX:FIXDASH2", "severity": "Functional Gap (Process will break)"},
    )
    test_client = _make_client(graph, client, settings)

    response = test_client.get("/dashboard/module-impact")

    assert response.status_code == 200
    assert "Functional Gap (Process will break)" in response.text


def test_gaps_view_renders_real_seeded_gap(client: FalkorDB, settings: Settings) -> None:
    graph = get_graph(client, settings=settings)
    graph.query(
        "MERGE (n:Entity {node_id: 'SOP:FIXDASHGAP'}) "
        "SET n.confidence = 'gap', n.node_type = 'SOP', n.module = 'governance', "
        "n.source_doc = 'TEST_FIXTURE'"
    )
    test_client = _make_client(graph, client, settings)

    response = test_client.get("/dashboard/gaps")

    assert response.status_code == 200
    assert "SOP:FIXDASHGAP" in response.text
    assert "Centrality ranking: low signal" in response.text


def test_impact_view_renders_real_seeded_chain(client: FalkorDB, settings: Settings) -> None:
    graph = get_graph(client, settings=settings)
    graph.query(
        "MERGE (a:Entity {node_id: 'PROC:FIXDASHAM01'}) "
        "SET a.confidence='documented', a.module='AM', a.node_type='BusinessProcess', "
        "a.source_doc='TEST_FIXTURE' "
        "MERGE (m1:Entity {node_id: 'PROC:FIXDASHMM13'}) "
        "SET m1.confidence='peripheral', m1.module='MM', m1.node_type='BusinessProcess', "
        "m1.source_doc='TEST_FIXTURE' "
        "MERGE (m2:Entity {node_id: 'PROC:FIXDASHMM18'}) "
        "SET m2.confidence='peripheral', m2.module='MM', m2.node_type='BusinessProcess', "
        "m2.source_doc='TEST_FIXTURE' "
        "MERGE (a)-[e1:LINKED_VIA_INVESTMENT]->(m1) "
        "SET e1.confidence='documented', e1.source_doc='TEST_FIXTURE' "
        "MERGE (m1)-[e2:FOLLOWED_BY]->(m2) "
        "SET e2.confidence='inferred', e2.source_doc='TEST_FIXTURE' "
        "MERGE (m2)-[e3:RECONCILES_TO]->(a) "
        "SET e3.confidence='documented', e3.source_doc='TEST_FIXTURE'"
    )
    test_client = _make_client(graph, client, settings)

    response = test_client.get("/dashboard/impact")

    assert response.status_code == 200
    assert "PROC:FIXDASHAM01" in response.text
    assert "Weakest link" in response.text


def test_ask_view_renders_form(client: FalkorDB, settings: Settings) -> None:
    graph = get_graph(client, settings=settings)
    _seed_transaction(graph, "TX:FIXDASHASK", "MM")  # graph key must exist before app construction
    test_client = _make_client(graph, client, settings)

    response = test_client.get("/dashboard/ask")

    assert response.status_code == 200
    assert '<textarea id="question"' in response.text
    assert 'id="ask-button"' in response.text
    assert "fetch('/ask'" in response.text


def test_agents_view_renders_all_six_agents(client: FalkorDB, settings: Settings) -> None:
    """Static reference page -- no live query, so no fixture data needed. Still
    seed one node first, matching the other views' 'graph key must exist before
    app construction' pattern."""
    graph = get_graph(client, settings=settings)
    _seed_transaction(graph, "TX:FIXDASHAGENTS", "MM")
    test_client = _make_client(graph, client, settings)

    response = test_client.get("/dashboard/agents")

    assert response.status_code == 200
    for agent_name in (
        "As-Is",
        "Migration-Mapping",
        "GxP-Compliance",
        "Cross-Module Impact",
        "Gaps",
        "Temporal",
    ):
        assert agent_name in response.text
