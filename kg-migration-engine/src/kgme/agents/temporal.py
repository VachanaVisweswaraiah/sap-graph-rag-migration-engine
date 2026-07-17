"""NOTE: This file has been sanitized for public/private portfolio use.
Business logic, domain-specific rules, and proprietary details have been masked.
The coding patterns, architecture, and technical implementation remain authentic.
[MASKED] tags indicate where original business logic has been replaced.

Temporal-Validity fallback: the graph has no date/document-age property on
any node or edge (confirmed against kg_nodes.csv/kg_edges.csv's actual
columns — see docs/AUDIT.md), so a question about whether a fact is still
valid today, or how old the documentation is, cannot be answered by querying
the graph.

Before this route existed, such a question fell through the classifier into
`compliance` and returned an unrelated GxP-risk-findings narrative — a wrong
answer, not an honest "not tracked." That's worse than a gap: a skimming user
could mistake it for a real response. This route returns a fixed, factual
explanation of the limitation instead, plus the two real dates known from the
hand-over README/HANDOFF_DETAIL.md.

No LLM call: the content is invariant (there is nothing in the graph to
query), so generating it via an LLM would only add hallucination risk for
zero benefit.
"""

from __future__ import annotations

TEMPORAL_ANSWER = (
    "This graph does not track document dates or a document-age property on any node or "
    "edge, so it cannot automatically tell you whether a specific fact is still current. "
    "What is known from the source documents themselves: the functional content behind the "
    "fully-documented processes (e.g. MM01, AM01) — requirements, design, and risk-analysis "
    "specs — dates to 2005-2007, from the original ECC 5.0 project. Only the authorization "
    "role specifications are maintained through 2014-2025. Any functional fact drawn from the "
    "2005-2007 generation of documents should be treated as \"true as of that document's "
    'date," not "true today," and re-validated against current NovaPharm Biologics process '
    "reality before being relied on for the S/4HANA migration."
)


def answer_temporal_question() -> str:
    return TEMPORAL_ANSWER
