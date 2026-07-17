"""NOTE: This file has been sanitized for public/private portfolio use.
Business logic, domain-specific rules, and proprietary details have been masked.
The coding patterns, architecture, and technical implementation remain authentic.
[MASKED] tags indicate where original business logic has been replaced.

Unit tests for db/gaps.py — row-shape mapping with a mocked graph, mirroring
tests/unit/test_mapping.py's mock style."""

from __future__ import annotations

from unittest.mock import MagicMock

from kgme.db.gaps import list_gap_nodes, list_inferred_edges


def test_list_gap_nodes_maps_rows_to_dataclasses() -> None:
    fake_graph = MagicMock()
    fake_graph.ro_query.return_value.result_set = [
        ["SOP:WI-000008", "SOP", "governance", "NOVAPHARM_INTERNAL"],
        ["DOC:FI01", "ReferencedDocument", "governance", None],
    ]

    nodes = list_gap_nodes(fake_graph)

    assert len(nodes) == 2
    assert nodes[0].node_id == "SOP:WI-000008"
    assert nodes[0].module == "governance"
    assert nodes[1].source_doc is None


def test_list_gap_nodes_returns_empty_list_when_no_gaps() -> None:
    fake_graph = MagicMock()
    fake_graph.ro_query.return_value.result_set = []

    assert list_gap_nodes(fake_graph) == []


def test_list_inferred_edges_maps_rows_to_dataclasses() -> None:
    fake_graph = MagicMock()
    fake_graph.ro_query.return_value.result_set = [
        ["QM:BATCH_RELEASE", "SUSPECTED_SOURCE", "SYS:LAB_SYSTEM", "DOC:RA_PROC01"],
        ["TX:MB1C", "MIGRATES_TO", "TX:MIGO", "DERIVED:s4_disposition"],
    ]

    edges = list_inferred_edges(fake_graph)

    assert len(edges) == 2
    assert edges[0].relation == "SUSPECTED_SOURCE"
    assert edges[1].target_id == "TX:MIGO"


def test_list_inferred_edges_returns_empty_list_when_none() -> None:
    fake_graph = MagicMock()
    fake_graph.ro_query.return_value.result_set = []

    assert list_inferred_edges(fake_graph) == []
