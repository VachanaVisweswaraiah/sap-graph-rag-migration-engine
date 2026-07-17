"""Unit tests for db/schema.py — fast, no Docker, no live graph."""

from __future__ import annotations

from pathlib import Path

import pytest

from kgme.core.exceptions import SchemaViolationError
from kgme.db.schema import DataDictionary, load_data_dictionary, validate_rows_against_dictionary

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DICTIONARY_PATH = REPO_ROOT / "data" / "raw" / "kg_data_dictionary.csv"


def test_load_data_dictionary_parses_real_file() -> None:
    dictionary = load_data_dictionary(DATA_DICTIONARY_PATH)

    assert "BusinessProcess" in dictionary.allowed_node_types
    assert len(dictionary.allowed_node_types) == 24
    # The dictionary's relation enum is known to be truncated ("...") — it lists
    # only some of the real relation types. SUSPECTED_SOURCE is documented;
    # SUSPECTED_USES_BWA is a real relation that is NOT (that's the whole point
    # of shape-validating relations instead of enum-checking them).
    assert "SUSPECTED_SOURCE" in dictionary.documented_relations
    assert "SUSPECTED_USES_BWA" not in dictionary.documented_relations


def _dictionary() -> DataDictionary:
    return DataDictionary(
        allowed_node_types=frozenset({"BusinessProcess", "Transaction"}),
        documented_relations=frozenset({"HAS_STEP"}),
    )


def test_unlisted_node_type_raises() -> None:
    nodes = [{"node_type": "NotARealType"}]
    with pytest.raises(SchemaViolationError, match="NotARealType"):
        validate_rows_against_dictionary(nodes, [], _dictionary())


def test_malformed_relation_raises() -> None:
    edges = [{"relation": "has_step"}]  # lowercase — not UPPER_SNAKE_CASE
    with pytest.raises(SchemaViolationError, match="has_step"):
        validate_rows_against_dictionary([], edges, _dictionary())


def test_shape_valid_but_undocumented_relation_does_not_raise() -> None:
    edges = [{"relation": "SUSPECTED_USES_BWA", "confidence": "documented"}]
    validate_rows_against_dictionary([], edges, _dictionary())


def test_documented_relation_passes_silently() -> None:
    edges = [{"relation": "HAS_STEP", "confidence": "documented"}]
    validate_rows_against_dictionary([], edges, _dictionary())


def test_node_with_inferred_confidence_raises() -> None:
    """Nodes are never `inferred` (CLAUDE.md's non-negotiable #1) -- only edges can
    be derived/AI-guessed. A node claiming confidence='inferred' must be rejected."""
    nodes = [{"node_type": "BusinessProcess", "confidence": "inferred"}]
    with pytest.raises(SchemaViolationError, match="inferred"):
        validate_rows_against_dictionary(nodes, [], _dictionary())


def test_edge_with_gap_confidence_raises() -> None:
    """Edges are never `gap` -- only nodes can be an undocumented gap."""
    edges = [{"relation": "HAS_STEP", "confidence": "gap"}]
    with pytest.raises(SchemaViolationError, match="gap"):
        validate_rows_against_dictionary([], edges, _dictionary())


def test_blank_confidence_raises() -> None:
    """A blank CSV cell loads as "" (see loader.py's row.get(field, "")), which is
    deliberately not in either confidence set -- must be rejected here rather than
    silently passing an IS NULL-only health check downstream."""
    nodes = [{"node_type": "BusinessProcess", "confidence": ""}]
    with pytest.raises(SchemaViolationError):
        validate_rows_against_dictionary(nodes, [], _dictionary())


def test_valid_node_and_edge_confidence_passes_silently() -> None:
    nodes = [{"node_type": "BusinessProcess", "confidence": "gap"}]
    edges = [{"relation": "HAS_STEP", "confidence": "inferred"}]
    validate_rows_against_dictionary(nodes, edges, _dictionary())
