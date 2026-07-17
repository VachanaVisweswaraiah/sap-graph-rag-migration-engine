"""Security-critical: a static read-only Cypher validator. This is defense-in-depth
ON TOP OF db/driver.py's read_only_query(), which routes through FalkorDB's
GRAPH.RO_QUERY — an engine-level enforcement verified in Phase 1 to reject write
clauses itself. A write-capable query reaching an agent path is a defect, not a nit,
per CLAUDE.md; every agent must call validate_read_only_cypher() before executing any
LLM-generated Cypher.

Bare `CALL { ... }` subqueries (no `IN TRANSACTIONS`) remain allowed — that's a
legitimate read-only construct in modern Cypher. Only the transaction-batching write
pattern (`CALL { ... } IN TRANSACTIONS`, used for large write operations) is banned.

_STRING_LITERAL must treat a backslash-escaped character (`\\'`, `\\"`, `\\\\`, ...) as
a single unit rather than a plain character — a naive `'[^']*'` pattern desyncs on an
escaped quote inside a literal (e.g. `'it\\'s'`), silently swallowing everything up to
the *next* unrelated quote — including a write clause — into what looks like "just a
string literal". This was a real bypass, found and fixed after live testing, not a
theoretical concern.
"""

from __future__ import annotations

import re

from kgme.core.exceptions import CypherGuardViolationError

_WRITE_CLAUSE = re.compile(r"\b(CREATE|MERGE|DELETE|SET|REMOVE|DROP)\b", re.IGNORECASE)
_LOAD_CSV = re.compile(r"\bLOAD\s+CSV\b", re.IGNORECASE)
_CALL_IN_TRANSACTIONS = re.compile(r"\bIN\s+TRANSACTIONS\b", re.IGNORECASE)
_STRING_LITERAL = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"")


def validate_read_only_cypher(cypher: str) -> None:
    """Strips string literals first so a property VALUE that happens to contain a
    write-clause-shaped word (e.g. WHERE n.label = 'Create New Batch Record') never
    false-positives, then scans the remainder for write clauses / LOAD CSV /
    CALL...IN TRANSACTIONS. Raises CypherGuardViolationError on any match."""
    stripped = _STRING_LITERAL.sub("", cypher)

    write_match = _WRITE_CLAUSE.search(stripped)
    if write_match:
        raise CypherGuardViolationError(
            f"query contains a write clause ({write_match.group(1).upper()}): {cypher!r}"
        )
    if _LOAD_CSV.search(stripped):
        raise CypherGuardViolationError(f"query contains LOAD CSV: {cypher!r}")
    if _CALL_IN_TRANSACTIONS.search(stripped):
        raise CypherGuardViolationError(f"query contains CALL ... IN TRANSACTIONS: {cypher!r}")
