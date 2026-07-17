"""NOTE: This file has been sanitized for public/private portfolio use.
Business logic, domain-specific rules, and proprietary details have been masked.
The coding patterns, architecture, and technical implementation remain authentic.
[MASKED] tags indicate where original business logic has been replaced.

As-Is Query Agent: natural-language question -> read-only Cypher -> results -> NL
answer, always citing confidence AND source_doc per fact per CLAUDE.md's non-negotiable
#1 ("every node/edge carries confidence, source_doc, source_ref -- no exceptions") and
non-negotiable that "an answer must never present an inferred/peripheral fact as
settled". Found live: answers were citing confidence but never source_doc/source_ref,
even though every fact in the graph has full provenance -- the data had traceability
the chat UI never surfaced, because neither prompt below asked for it.

Two LLM calls: one tool-forced call to generate Cypher (far more reliable than
regex-extracting a query from free text), one plain call to compose the answer from
the query results. Every generated query passes through cypher_guard AND
db.driver.read_only_query's engine-level GRAPH.RO_QUERY enforcement before being
executed — two independent layers, per CLAUDE.md's security-critical requirement that
a write-capable query in an agent path is a defect, not a nit.

On any failure (cypher_guard rejection, Anthropic API error), degrades to a safe
"could not answer" response — never fabricates, never crashes the caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import anthropic
import structlog
from falkordb import Graph

from kgme.agents.cypher_guard import validate_read_only_cypher
from kgme.core.exceptions import CypherGuardViolationError
from kgme.db.driver import read_only_query

_CYPHER_TOOL: dict[str, Any] = {
    "name": "generate_cypher_query",
    "description": (
        "Generate a single read-only Cypher query against the FalkorDB knowledge "
        "graph to answer the user's question."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "cypher": {"type": "string", "description": "A single read-only Cypher query."},
            "explanation": {
                "type": "string",
                "description": "Brief explanation of what the query retrieves.",
            },
        },
        "required": ["cypher"],
    },
}

_CANNOT_ANSWER_SAFELY = (
    "I can't answer that safely — the generated query wasn't read-only, so it was "
    "blocked before running."
)
_CANNOT_ANSWER_API_ERROR = (
    "I couldn't reach the language model to answer that question. Please try again."
)


@dataclass(frozen=True)
class AsIsAnswer:
    question: str
    cypher: str | None
    answer: str
    blocked: bool


class AsIsQueryAgent:
    def __init__(
        self,
        client: anthropic.Anthropic,
        graph: Graph,
        *,
        model: str,
        schema_context: str,
        logger: structlog.stdlib.BoundLogger,
    ) -> None:
        self._client = client
        self._graph = graph
        self._model = model
        self._schema_context = schema_context
        self._logger = logger

    def ask(self, question: str) -> AsIsAnswer:
        try:
            cypher = self._generate_cypher(question)
        except anthropic.AnthropicError as exc:
            self._logger.error("agents.as_is.api_error", stage="cypher_generation", error=str(exc))
            return AsIsAnswer(
                question=question, cypher=None, answer=_CANNOT_ANSWER_API_ERROR, blocked=False
            )

        self._logger.info("agents.as_is.cypher_generated", question=question, cypher=cypher)

        try:
            validate_read_only_cypher(cypher)
        except CypherGuardViolationError as exc:
            self._logger.error(
                "agents.as_is.cypher_guard_violation",
                question=question,
                cypher=cypher,
                error=str(exc),
            )
            return AsIsAnswer(
                question=question, cypher=None, answer=_CANNOT_ANSWER_SAFELY, blocked=True
            )

        result = read_only_query(self._graph, cypher)
        columns = [col[1] for col in result.header]
        rows = [dict(zip(columns, row, strict=True)) for row in result.result_set]

        try:
            answer = self._compose_answer(question, cypher, rows)
        except anthropic.AnthropicError as exc:
            self._logger.error("agents.as_is.api_error", stage="answer_composition", error=str(exc))
            return AsIsAnswer(
                question=question, cypher=cypher, answer=_CANNOT_ANSWER_API_ERROR, blocked=False
            )

        self._logger.info("agents.as_is.answer_composed", question=question)
        return AsIsAnswer(question=question, cypher=cypher, answer=answer, blocked=False)

    def _generate_cypher(self, question: str) -> str:
        response = self._client.messages.create(  # type: ignore[call-overload]
            model=self._model,
            max_tokens=1024,
            system=(
                f"{self._schema_context}\n"
                "You generate a single read-only Cypher query to answer the user's "
                "question against this graph. Never generate CREATE, MERGE, DELETE, "
                "SET, REMOVE, DROP, LOAD CSV, or any write operation — only MATCH/"
                "WITH/UNWIND/RETURN style read queries. Always include the "
                "`confidence` property of any node/edge you return, AND its "
                "`source_doc` (and `source_ref` when available) — every fact must "
                "be traceable back to where it came from, not just how much it can "
                "be trusted."
            ),
            messages=[{"role": "user", "content": question}],
            tools=[_CYPHER_TOOL],
            tool_choice={"type": "tool", "name": "generate_cypher_query"},
        )
        for block in response.content:
            if block.type == "tool_use":
                return str(block.input["cypher"])
        raise CypherGuardViolationError("model did not return a tool_use cypher block")

    def _compose_answer(self, question: str, cypher: str, rows: list[dict[str, Any]]) -> str:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=(
                "You compose a natural-language answer from Cypher query results "
                "against a GxP knowledge graph. For every fact you state, cite its "
                "`confidence` value AND its `source_doc` in brackets, e.g. "
                "'[documented, source: RA_PROC01]' or '[inferred — unverified, "
                "source: DERIVED:s4_disposition]'. If a row has no source_doc "
                "value, say '[<confidence>, source: not recorded]' rather than "
                "omitting the source mention entirely. Never present an inferred "
                "or peripheral fact as settled fact. If the results are empty, "
                "say so plainly — never invent an answer."
            ),
            messages=[
                {
                    "role": "user",
                    "content": (f"Question: {question}\nCypher used: {cypher}\nResults: {rows}"),
                }
            ],
        )
        for block in response.content:
            if block.type == "text":
                return block.text
        return "No answer text was returned."
