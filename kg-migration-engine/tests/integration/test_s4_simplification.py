"""Integration tests for enrichment/s4_simplification.py against a real FalkorDB."""

from __future__ import annotations

from pathlib import Path

import pytest
from falkordb import FalkorDB

from kgme.config import Settings
from kgme.core.observability import get_logger
from kgme.db.driver import get_graph
from kgme.db.loader import load_graph
from kgme.enrichment.s4_simplification import enrich_graph, load_catalog
from tests.integration.conftest import DATA_DICTIONARY_PATH, EDGES_FIXTURE_PATH, NODES_FIXTURE_PATH

pytestmark = pytest.mark.integration

S4_FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "s4_catalog_fixture.json"


def _load_kg_fixture(client: FalkorDB, settings: Settings) -> None:
    load_graph(
        client,
        settings=settings,
        nodes_path=NODES_FIXTURE_PATH,
        edges_path=EDGES_FIXTURE_PATH,
        data_dictionary_path=DATA_DICTIONARY_PATH,
    )


def test_enrich_graph_adds_properties_to_matched_node(client: FalkorDB, settings: Settings) -> None:
    _load_kg_fixture(client, settings)
    graph = get_graph(client, settings=settings)
    rows = load_catalog(S4_FIXTURE_PATH)
    logger = get_logger("test")

    summary = enrich_graph(graph, rows, logger)

    assert summary.matched_count == 1
    row = graph.query(
        "MATCH (n:Entity {node_id: 'TX:MM01_MAIN'}) "
        "RETURN n.s4_status, n.s4_target, n.s4_confidence, n.s4_source_doc, n.s4_source_ref"
    ).result_set[0]
    assert row[0] == "Mandatory"
    assert row[1] == "Sample Materials Management Simplification"
    assert row[2] == "inferred"
    assert row[3] == "DERIVED:SAP_SIMPLIFICATION_LIST"
    assert "SIM0001" in row[4]


def test_enrich_graph_does_not_touch_original_provenance(
    client: FalkorDB, settings: Settings
) -> None:
    _load_kg_fixture(client, settings)
    graph = get_graph(client, settings=settings)
    original = graph.query(
        "MATCH (n:Entity {node_id: 'TX:MM01_MAIN'}) RETURN n.confidence, n.source_doc, n.source_ref"
    ).result_set[0]

    rows = load_catalog(S4_FIXTURE_PATH)
    enrich_graph(graph, rows, get_logger("test"))

    after = graph.query(
        "MATCH (n:Entity {node_id: 'TX:MM01_MAIN'}) RETURN n.confidence, n.source_doc, n.source_ref"
    ).result_set[0]
    assert after == original


def test_enrich_graph_is_idempotent(client: FalkorDB, settings: Settings) -> None:
    _load_kg_fixture(client, settings)
    graph = get_graph(client, settings=settings)
    rows = load_catalog(S4_FIXTURE_PATH)
    logger = get_logger("test")

    enrich_graph(graph, rows, logger)
    first = graph.query(
        "MATCH (n:Entity {node_id: 'TX:MM01_MAIN'}) RETURN n.s4_status, n.s4_target"
    ).result_set[0]

    enrich_graph(graph, rows, logger)
    second = graph.query(
        "MATCH (n:Entity {node_id: 'TX:MM01_MAIN'}) RETURN n.s4_status, n.s4_target"
    ).result_set[0]

    assert first == second


def test_enrich_graph_reports_unmatched_and_skipped(client: FalkorDB, settings: Settings) -> None:
    _load_kg_fixture(client, settings)
    graph = get_graph(client, settings=settings)
    rows = load_catalog(S4_FIXTURE_PATH)

    summary = enrich_graph(graph, rows, get_logger("test"))

    assert summary.unmatched == []
    assert summary.skipped == []


def test_enrich_graph_never_creates_new_nodes(client: FalkorDB, settings: Settings) -> None:
    _load_kg_fixture(client, settings)
    graph = get_graph(client, settings=settings)
    before_count = graph.query("MATCH (n) RETURN count(n)").result_set[0][0]

    rows = load_catalog(S4_FIXTURE_PATH)
    enrich_graph(graph, rows, get_logger("test"))

    after_count = graph.query("MATCH (n) RETURN count(n)").result_set[0][0]
    assert after_count == before_count
