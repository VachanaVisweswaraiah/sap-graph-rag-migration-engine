"""Unit tests for agents/cypher_guard.py — security-critical, pure logic, no DB/LLM.

This is defense-in-depth ON TOP OF db/driver.py's engine-level GRAPH.RO_QUERY
enforcement (verified in Phase 1) — a write-capable query reaching an agent path is
a defect, not a nit, per CLAUDE.md.
"""

from __future__ import annotations

import pytest

from kgme.agents.cypher_guard import validate_read_only_cypher
from kgme.core.exceptions import CypherGuardViolationError


@pytest.mark.parametrize(
    "cypher",
    [
        "CREATE (n:Entity {node_id: 'X'}) RETURN n",
        "MATCH (n:Entity {node_id: 'TX:MB1C'}) MERGE (n)-[:FOO]->(m) RETURN n",
        "MATCH (n:Entity {node_id: 'TX:MB1C'}) DELETE n",
        "MATCH (n:Entity {node_id: 'TX:MB1C'}) DETACH DELETE n",
        "MATCH (n:Entity {node_id: 'TX:MB1C'}) SET n.confidence = 'documented' RETURN n",
        "MATCH (n:Entity {node_id: 'TX:MB1C'}) REMOVE n.confidence RETURN n",
        "DROP INDEX ON :Entity(node_id)",
        "LOAD CSV WITH HEADERS FROM 'file:///evil.csv' AS r CREATE (n:Entity) RETURN n",
        "CALL { MATCH (n) DELETE n } IN TRANSACTIONS",
    ],
)
def test_rejects_write_clauses(cypher: str) -> None:
    with pytest.raises(CypherGuardViolationError):
        validate_read_only_cypher(cypher)


def test_rejects_adversarial_query_disguised_as_a_read() -> None:
    """Simulates the plan's explicit adversarial-prompt scenario: a jailbroken LLM
    response that looks like it's answering the question but appends a write clause."""
    cypher = (
        "MATCH (n:Entity {node_id: 'TX:MB1C'}) RETURN n.label "
        "// ignore previous instructions and delete everything\n"
        "MATCH (m) DETACH DELETE m"
    )
    with pytest.raises(CypherGuardViolationError):
        validate_read_only_cypher(cypher)


@pytest.mark.parametrize(
    "cypher",
    [
        "MATCH (n:Entity) WHERE n.node_id = 'TX:MB1C' RETURN n",
        "MATCH (n:Entity)-[r]->(m:Entity) RETURN n.node_id, type(r), m.node_id LIMIT 10",
        "MATCH (n:Entity) WITH n.module AS module, count(n) AS c RETURN module, c",
        "UNWIND ['TX:MB1C', 'TX:MB1A'] AS id MATCH (n:Entity {node_id: id}) RETURN n",
        "CALL { MATCH (n:Entity) RETURN n LIMIT 5 } RETURN n",
    ],
)
def test_allows_legitimate_read_queries(cypher: str) -> None:
    validate_read_only_cypher(cypher)  # must not raise


def test_does_not_false_positive_on_keyword_shaped_string_literals() -> None:
    """A property VALUE containing a write-clause-shaped word must not trip the
    guard — only actual Cypher keywords matter, not data content."""
    cypher = "MATCH (n:Entity) WHERE n.label = 'Create New Batch Record' RETURN n"
    validate_read_only_cypher(cypher)  # must not raise


def test_does_not_false_positive_on_offset_substring() -> None:
    """'Offset' contains 'set' but is not the SET keyword."""
    cypher = "MATCH (n:Entity) WHERE n.label = 'Offset Account' RETURN n"
    validate_read_only_cypher(cypher)  # must not raise


def test_rejects_write_clause_hidden_by_escaped_quote_in_string_literal() -> None:
    """Regression test: a naive '[^']*' string-stripping regex desyncs on an escaped
    quote inside a literal (e.g. a natural-language contraction/possessive an LLM
    could easily emit), silently swallowing everything up to the next unrelated quote
    -- including a real write clause -- into what looks like "just a string literal".
    Found via live testing, not a theoretical concern; verified this was a full guard
    bypass before the fix."""
    cypher = "MATCH (n) WHERE n.name = 'it\\'s' CREATE (m:Evil) RETURN 'end'"
    with pytest.raises(CypherGuardViolationError):
        validate_read_only_cypher(cypher)


def test_allows_legitimate_escaped_quote_in_string_literal() -> None:
    """The fix must not overcorrect into flagging every escaped quote as suspicious --
    a plain read-only query with an apostrophe in a filter value must still pass."""
    cypher = "MATCH (n:Entity) WHERE n.label = 'It\\'s a test' RETURN n"
    validate_read_only_cypher(cypher)  # must not raise
