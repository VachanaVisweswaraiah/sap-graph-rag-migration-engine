"""Integration tests for db/health.py against a real (testcontainers) FalkorDB."""

from __future__ import annotations

import pytest
from falkordb import FalkorDB

from kgme.config import Settings
from kgme.db.driver import get_graph
from kgme.db.health import run_health_checks
from kgme.db.loader import load_graph
from tests.integration.conftest import DATA_DICTIONARY_PATH, EDGES_FIXTURE_PATH, NODES_FIXTURE_PATH

pytestmark = pytest.mark.integration


def test_health_checks_fail_before_any_load(client: FalkorDB, settings: Settings) -> None:
    report = run_health_checks(client, settings=settings, deep=True)

    assert report.healthy is False
    constraints_check = next(c for c in report.checks if c.name == "constraints")
    assert constraints_check.ok is False


def test_health_checks_pass_after_successful_load(client: FalkorDB, settings: Settings) -> None:
    load_graph(
        client,
        settings=settings,
        nodes_path=NODES_FIXTURE_PATH,
        edges_path=EDGES_FIXTURE_PATH,
        data_dictionary_path=DATA_DICTIONARY_PATH,
    )

    report = run_health_checks(client, settings=settings, deep=True)

    assert report.healthy is True
    assert all(c.ok for c in report.checks)


def test_shallow_health_check_skips_provenance(client: FalkorDB, settings: Settings) -> None:
    load_graph(
        client,
        settings=settings,
        nodes_path=NODES_FIXTURE_PATH,
        edges_path=EDGES_FIXTURE_PATH,
        data_dictionary_path=DATA_DICTIONARY_PATH,
    )

    report = run_health_checks(client, settings=settings, deep=False)

    assert {c.name for c in report.checks} == {"connectivity", "graph_selected", "constraints"}


def test_provenance_check_fails_when_a_node_loses_provenance(
    client: FalkorDB, settings: Settings
) -> None:
    load_graph(
        client,
        settings=settings,
        nodes_path=NODES_FIXTURE_PATH,
        edges_path=EDGES_FIXTURE_PATH,
        data_dictionary_path=DATA_DICTIONARY_PATH,
    )
    graph = get_graph(client, settings=settings)
    graph.query("MATCH (n:Entity {node_id: 'PROC:MM01'}) SET n.confidence = NULL")

    report = run_health_checks(client, settings=settings, deep=True)

    assert report.healthy is False
    provenance_check = next(c for c in report.checks if c.name == "provenance_complete")
    assert provenance_check.ok is False
