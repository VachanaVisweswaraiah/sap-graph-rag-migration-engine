"""Integration tests for agents/graph.py's compiled orchestration graph against a
real FalkorDB, with a MOCKED Anthropic client (no real API calls — same pattern as
test_as_is.py). One test per route, verifying the compiled graph actually queries
the real seeded graph rather than a stub.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from falkordb import FalkorDB, Graph

from kgme.agents.graph import build_orchestration_graph, route_question
from kgme.config import Settings
from kgme.core.observability import get_logger
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


def test_route_as_is_delegates_to_as_is_agent(client: FalkorDB, settings: Settings) -> None:
    graph = get_graph(client, settings=settings)
    _seed_transaction(graph, "TX:FIXGRAPH1", "FIXMODC")

    # Three sequential LLM calls: classify, then as_is's own cypher-generation and
    # answer-composition calls.
    fake_client = MagicMock()
    fake_client.messages.create.side_effect = [
        _tool_use_response("as_is"),
        MagicMock(
            content=[
                MagicMock(
                    type="tool_use",
                    input={"cypher": "MATCH (n:Entity {node_id: 'TX:FIXGRAPH1'}) RETURN n.node_id"},
                )
            ]
        ),
        _text_response("TX:FIXGRAPH1 exists [documented]."),
    ]

    app = build_orchestration_graph(
        fake_client, graph, model="claude-sonnet-5", schema_context="", logger=get_logger("test")
    )
    result = route_question(app, "Does TX:FIXGRAPH1 exist?")

    assert result["route"] == "as_is"
    assert "documented" in result["final_answer"]
    assert result["blocked"] is False


def test_route_mapping_queries_real_seeded_coverage(client: FalkorDB, settings: Settings) -> None:
    graph = get_graph(client, settings=settings)
    _seed_transaction(graph, "TX:FIXGRAPH2", "FIXMODD")
    _seed_transaction(graph, "TX:FIXGRAPH3", "FIXMODD")
    graph.query(
        "MATCH (s:Entity {node_id:'TX:FIXGRAPH2'}) "
        "MERGE (t:Entity {node_id:'TX:FIXGRAPHTGT'}) "
        "MERGE (s)-[r:MIGRATES_TO]->(t) SET r.confidence='inferred', r.source_doc='TEST_FIXTURE'"
    )

    fake_client = MagicMock()
    fake_client.messages.create.side_effect = [
        _tool_use_response("mapping", "FIXMODD"),
        _text_response("FIXMODD: 1/2 transactions mapped."),
    ]

    app = build_orchestration_graph(
        fake_client, graph, model="claude-sonnet-5", schema_context="", logger=get_logger("test")
    )
    result = route_question(app, "What's mapped in FIXMODD?")

    assert result["route"] == "mapping"
    assert result["module"] == "FIXMODD"
    assert "1/2" in result["final_answer"]


def test_route_compliance_queries_real_seeded_findings(
    client: FalkorDB, settings: Settings
) -> None:
    graph = get_graph(client, settings=settings)
    graph.query(
        "MERGE (s:Entity {node_id:'TX:FIXGRAPHSRC'}) "
        "SET s.confidence='documented', s.source_doc='TEST_FIXTURE' "
        "MERGE (t:Entity {node_id:'TX:FIXGRAPHDST'}) "
        "SET t.confidence='documented', t.source_doc='TEST_FIXTURE' "
        "MERGE (s)-[r:SUSPECTED_USES_BWA]->(t) "
        "SET r.confidence='inferred', r.source_doc='TEST_FIXTURE'"
    )

    fake_client = MagicMock()
    fake_client.messages.create.side_effect = [
        _tool_use_response("compliance"),
        _text_response("Found an inferred edge between TX:FIXGRAPHSRC and TX:FIXGRAPHDST."),
    ]

    app = build_orchestration_graph(
        fake_client, graph, model="claude-sonnet-5", schema_context="", logger=get_logger("test")
    )
    result = route_question(app, "What are the compliance risks?")

    assert result["route"] == "compliance"
    assert "TX:FIXGRAPHSRC" in result["final_answer"]
