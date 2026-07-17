"""Integration tests for api/app_factory.py's FastAPI app against a real FalkorDB,
with a MOCKED Anthropic client (no real API calls — same pattern as
test_as_is.py/test_graph.py). Uses TestClient(create_app(...)), never imports
api/main.py (which would build a real client/API connection at import time).
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


def _tool_use_response(route: str, module: str | None = None) -> MagicMock:
    block = MagicMock(type="tool_use", input={"route": route, "module": module})
    return MagicMock(content=[block])


def _text_response(text: str) -> MagicMock:
    return MagicMock(content=[MagicMock(type="text", text=text)])


def _seed_transaction(graph: Graph, node_id: str, module: str) -> None:
    graph.query(
        "MERGE (n:Entity:Transaction {node_id: $node_id}) "
        "SET n.module = $module, n.node_type = 'Transaction', n.confidence = 'documented', "
        "n.source_doc = 'TEST_FIXTURE', n.source_ref = 'test', n.label = 'Fixture TX'",
        {"node_id": node_id, "module": module},
    )


def _make_client(
    graph: Graph, falkordb_client: FalkorDB, settings: Settings
) -> tuple[TestClient, MagicMock]:
    fake_anthropic = MagicMock()
    app = create_app(
        client=falkordb_client, graph=graph, anthropic_client=fake_anthropic, settings=settings
    )
    return TestClient(app), fake_anthropic


def test_ask_endpoint_returns_routed_answer(client: FalkorDB, settings: Settings) -> None:
    graph = get_graph(client, settings=settings)
    _seed_transaction(graph, "TX:FIXAPI1", "MM")
    test_client, fake_anthropic = _make_client(graph, client, settings)
    fake_anthropic.messages.create.side_effect = [
        _tool_use_response("mapping", "MM"),
        _text_response("MM mapping narrative"),
    ]

    response = test_client.post("/ask", json={"question": "what is mapped in MM"})

    assert response.status_code == 200
    body = response.json()
    assert body["route"] == "mapping"
    assert body["answer"] == "MM mapping narrative"
    assert body["blocked"] is False


def test_gaps_endpoint_returns_real_seeded_gap(client: FalkorDB, settings: Settings) -> None:
    graph = get_graph(client, settings=settings)
    graph.query(
        "MERGE (n:Entity {node_id: 'SOP:FIXAPIGAP'}) "
        "SET n.confidence = 'gap', n.node_type = 'SOP', n.module = 'governance', "
        "n.source_doc = 'TEST_FIXTURE'"
    )
    test_client, _ = _make_client(graph, client, settings)

    response = test_client.get("/gaps")

    assert response.status_code == 200
    body = response.json()
    ids = {n["node_id"] for n in body["gap_nodes"]}
    assert "SOP:FIXAPIGAP" in ids


def test_module_impact_endpoint_returns_real_coverage(client: FalkorDB, settings: Settings) -> None:
    graph = get_graph(client, settings=settings)
    _seed_transaction(graph, "TX:FIXAPI2", "AM")
    _seed_transaction(graph, "TX:FIXAPI3", "AM")
    test_client, _ = _make_client(graph, client, settings)

    response = test_client.get("/module/AM/impact")

    assert response.status_code == 200
    body = response.json()
    assert body["module"] == "AM"
    assert body["mapping_coverage"]["total_transactions"] == 2
    assert body["mapping_coverage"]["mapped_transactions"] == 0


def test_module_impact_endpoint_reports_s4_catalog_flags(
    client: FalkorDB, settings: Settings
) -> None:
    graph = get_graph(client, settings=settings)
    _seed_transaction(graph, "TX:FIXS4A", "AM")
    graph.query(
        "MATCH (n:Entity {node_id: $node_id}) "
        "SET n.s4_status = 'Deprecated', n.s4_severity = $severity",
        {"node_id": "TX:FIXS4A", "severity": "Functional Gap (Process will break)"},
    )
    test_client, _ = _make_client(graph, client, settings)

    response = test_client.get("/module/AM/impact")

    assert response.status_code == 200
    assert response.json()["s4_flagged_nodes"] == {"Functional Gap (Process will break)": 1}


def test_module_impact_endpoint_404s_on_unknown_module(
    client: FalkorDB, settings: Settings
) -> None:
    graph = get_graph(client, settings=settings)
    _seed_transaction(graph, "TX:FIXAPI4", "MM")  # graph key must exist before app construction
    test_client, _ = _make_client(graph, client, settings)

    response = test_client.get("/module/NOT_A_MODULE/impact")

    assert response.status_code == 404


def _seed_reconciliation_chain(graph: Graph) -> None:
    graph.query(
        "MERGE (a:Entity {node_id: 'PROC:FIXAM01'}) "
        "SET a.confidence='documented', a.module='AM', a.node_type='BusinessProcess', "
        "a.source_doc='TEST_FIXTURE' "
        "MERGE (m1:Entity {node_id: 'PROC:FIXMM13'}) "
        "SET m1.confidence='peripheral', m1.module='MM', m1.node_type='BusinessProcess', "
        "m1.source_doc='TEST_FIXTURE' "
        "MERGE (m2:Entity {node_id: 'PROC:FIXMM18'}) "
        "SET m2.confidence='peripheral', m2.module='MM', m2.node_type='BusinessProcess', "
        "m2.source_doc='TEST_FIXTURE' "
        "MERGE (a)-[e1:LINKED_VIA_INVESTMENT]->(m1) "
        "SET e1.confidence='documented', e1.source_doc='TEST_FIXTURE' "
        "MERGE (m1)-[e2:FOLLOWED_BY]->(m2) "
        "SET e2.confidence='inferred', e2.source_doc='TEST_FIXTURE' "
        "MERGE (m2)-[e3:RECONCILES_TO]->(a) "
        "SET e3.confidence='documented', e3.source_doc='TEST_FIXTURE'"
    )


def test_impact_chains_endpoint_returns_real_seeded_chain(
    client: FalkorDB, settings: Settings
) -> None:
    graph = get_graph(client, settings=settings)
    _seed_reconciliation_chain(graph)
    test_client, _ = _make_client(graph, client, settings)

    response = test_client.get("/impact/chains")

    assert response.status_code == 200
    body = response.json()
    ids = {c["start"]["node_id"] for c in body["chains"]}
    assert "PROC:FIXAM01" in ids
    chain = next(c for c in body["chains"] if c["start"]["node_id"] == "PROC:FIXAM01")
    assert chain["mid1"]["node_id"] == "PROC:FIXMM13"
    assert chain["mid2"]["node_id"] == "PROC:FIXMM18"
    assert chain["weakest_link_confidence"] == "inferred"


def test_health_endpoint_returns_healthy(client: FalkorDB, settings: Settings) -> None:
    graph = get_graph(client, settings=settings)
    _seed_transaction(graph, "TX:FIXAPI5", "MM")  # graph key must exist before app construction
    test_client, _ = _make_client(graph, client, settings)

    response = test_client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert "healthy" in body
    assert isinstance(body["checks"], list)
