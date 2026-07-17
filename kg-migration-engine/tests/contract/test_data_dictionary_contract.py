"""
NOTE: This file has been sanitized for public/private portfolio use.
Business logic, domain-specific rules, and proprietary details have been masked.
The coding patterns, architecture, and technical implementation remain authentic.
[MASKED] tags indicate where original business logic has been replaced.

Contract tests: validate the REAL data/raw CSVs against kg_data_dictionary.csv.

These are about the data, not the code — they'd break if someone edited the source
CSVs in a way that violates the schema, independent of whether the loader itself
works. Run less often than unit/integration (see .github/workflows/ci.yml).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kgme.db.loader import read_csv_rows
from kgme.db.schema import load_data_dictionary, validate_rows_against_dictionary

pytestmark = pytest.mark.contract

REPO_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = REPO_ROOT / "data" / "raw"

NODES_PATH = RAW_DIR / "kg_nodes.csv"
EDGES_PATH = RAW_DIR / "kg_edges.csv"
DATA_DICTIONARY_PATH = RAW_DIR / "kg_data_dictionary.csv"

EXPECTED_NODE_COUNT = 55
EXPECTED_EDGE_COUNT = 47

EXPECTED_NODE_HEADERS = (
    "node_id",
    "node_type",
    "label",
    "module",
    "gxp_classification",
    "confidence",
    "source_doc",
    "source_ref",
    "notes",
)
EXPECTED_EDGE_HEADERS = (
    "edge_id",
    "source_id",
    "target_id",
    "relation",
    "confidence",
    "source_doc",
    "source_ref",
    "notes",
)


def test_node_count_is_55() -> None:
    assert len(read_csv_rows(NODES_PATH)) == EXPECTED_NODE_COUNT


def test_edge_count_is_47() -> None:
    assert len(read_csv_rows(EDGES_PATH)) == EXPECTED_EDGE_COUNT


def test_node_headers_match_data_dictionary() -> None:
    nodes = read_csv_rows(NODES_PATH)
    assert tuple(nodes[0].keys()) == EXPECTED_NODE_HEADERS


def test_edge_headers_match_data_dictionary() -> None:
    edges = read_csv_rows(EDGES_PATH)
    assert tuple(edges[0].keys()) == EXPECTED_EDGE_HEADERS


def test_real_data_passes_schema_validation() -> None:
    """Every node_type is in the dictionary's closed enum, every relation is
    UPPER_SNAKE_CASE shaped — the same check load_graph() runs before any write."""
    nodes = read_csv_rows(NODES_PATH)
    edges = read_csv_rows(EDGES_PATH)
    dictionary = load_data_dictionary(DATA_DICTIONARY_PATH)

    validate_rows_against_dictionary(nodes, edges, dictionary)


def test_no_dangling_edge_endpoints() -> None:
    nodes = read_csv_rows(NODES_PATH)
    edges = read_csv_rows(EDGES_PATH)
    node_ids = {row["node_id"] for row in nodes}

    dangling = [
        e["edge_id"]
        for e in edges
        if e["source_id"] not in node_ids or e["target_id"] not in node_ids
    ]
    assert dangling == []


def test_every_node_has_confidence_and_source_doc() -> None:
    nodes = read_csv_rows(NODES_PATH)
    missing = [n["node_id"] for n in nodes if not n["confidence"] or not n["source_doc"]]
    assert missing == []


def test_every_edge_has_confidence_and_source_doc() -> None:
    edges = read_csv_rows(EDGES_PATH)
    missing = [e["edge_id"] for e in edges if not e["confidence"] or not e["source_doc"]]
    assert missing == []


def test_no_node_is_ever_inferred() -> None:
    """CLAUDE.md hard rule: no node is ever `inferred` — only edges are."""
    nodes = read_csv_rows(NODES_PATH)
    assert all(n["confidence"] != "inferred" for n in nodes)


def test_no_edge_is_ever_gap() -> None:
    """CLAUDE.md hard rule: no edge is ever `gap` — only nodes are."""
    edges = read_csv_rows(EDGES_PATH)
    assert all(e["confidence"] != "gap" for e in edges)


def test_flagship_finding_present() -> None:
    """The SUSPECTED_SOURCE QM:BATCH_RELEASE -> SYS:LAB_SYSTEM edge must exist and be
    inferred — the compliance agent's headline finding (RA_PROC01 risk #7)."""
    edges = read_csv_rows(EDGES_PATH)
    match = next(
        (
            e
            for e in edges
            if e["relation"] == "SUSPECTED_SOURCE"
            and e["source_id"] == "QM:BATCH_RELEASE"
            and e["target_id"] == "SYS:LAB_SYSTEM"
        ),
        None,
    )
    assert match is not None
    assert match["confidence"] == "inferred"
