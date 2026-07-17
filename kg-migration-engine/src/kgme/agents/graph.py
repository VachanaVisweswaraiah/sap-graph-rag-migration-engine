"""NOTE: This file has been sanitized for public/private portfolio use.
Business logic, domain-specific rules, and proprietary details have been masked.
The coding patterns, architecture, and technical implementation remain authentic.
[MASKED] tags indicate where original business logic has been replaced.

LangGraph orchestration: routes an incoming question to whichever of the six
agents (As-Is, Migration-Mapping, GxP-Compliance, Cross-Module Impact, Gaps,
Temporal-Validity) answers it, then returns one coherent NL answer.

The router only ever picks a route (+ an optional module scope) — it never
generates Cypher itself. Each downstream agent keeps its own independent,
already-verified query trust model (LLM-generated-and-guarded for As-Is, fixed
Cypher for Mapping/Compliance); routing adds a dispatch layer on top, it does not
change what any agent is allowed to do.

Module extraction fails closed: the classifier only sets `module` when the
question clearly names one of the four real module values, matching the
disposition parser's "never guess, report honestly instead" pattern from Phase 2.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict, cast

import anthropic
import structlog
from falkordb import Graph
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from kgme.agents.as_is import AsIsQueryAgent
from kgme.agents.compliance import build_compliance_report
from kgme.agents.gaps import build_gaps_report
from kgme.agents.impact import build_impact_report
from kgme.agents.mapping import MODULES, build_mapping_report
from kgme.agents.temporal import answer_temporal_question

Route = Literal["as_is", "mapping", "compliance", "impact", "gaps", "temporal", "error"]

_CLASSIFICATION_UNAVAILABLE = (
    "I couldn't reach the language model to route your question. Please try again."
)

_CLASSIFY_TOOL: dict[str, Any] = {
    "name": "classify_question",
    "description": (
        "Classify a question about a SAP ECC-to-S/4HANA migration knowledge graph "
        "into exactly one route."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "route": {
                "type": "string",
                "enum": [
                    "as_is",
                    "mapping",
                    "compliance",
                    "impact",
                    "gaps",
                    "temporal",
                ],
                "description": (
                    "'as_is' for arbitrary factual questions about the graph "
                    "(what uses what, who authorizes what, etc). 'mapping' for "
                    "questions about MIGRATES_TO coverage / what's mapped to "
                    "S/4HANA. 'compliance' for questions framed as GxP RISK or "
                    "FINDINGS (e.g. 'what compliance risks exist', 'which "
                    "findings are inferred'), especially the Lab System/batch-release "
                    "flagship — not a plain listing of missing documents (use "
                    "'gaps' for that). ALSO 'compliance' for a BROAD or "
                    "module-level SAP Simplification Catalog / S/4HANA "
                    "migration-readiness survey (e.g. 'are there any "
                    "Simplification Catalog findings for module Y', 'what will "
                    "break in S/4 overall') — route these to 'compliance' even "
                    "when phrased as a plain listing rather than explicit risk "
                    "language; never 'gaps', which has no S/4-catalog awareness "
                    "at all. But a question about ONE SPECIFIC named node's S/4 "
                    "catalog status (e.g. 'what does the catalog say about "
                    "TX:AS21', 'is TX:MB1C deprecated in S/4') is 'as_is' — a "
                    "targeted single-node lookup, not a survey; routing it to "
                    "'compliance' forces an irrelevant flagship-finding preamble "
                    "onto an answer that doesn't need it. 'impact' for questions "
                    "about cross-module dependencies or reconciliation chains — "
                    "how MM and AM processes connect (e.g. an investment "
                    "request, purchase order, invoice verification, and asset "
                    "chain), or which hop in such a chain is weakest/riskiest. "
                    "'gaps' for questions asking to list or inventory which "
                    "specific documents, SOPs, or nodes are missing / "
                    "gap-confidence, or which edges are only inferred — content "
                    "native to this graph's own confidence model, NOT SAP "
                    "Simplification Catalog findings (those are 'compliance'). "
                    "'temporal' for questions about whether documented facts are "
                    "still valid/current today, how old the underlying "
                    "documentation is, or what needs re-validation due to age — "
                    "the graph has no date property, so this route always "
                    "explains that limitation honestly rather than guessing."
                ),
            },
            "module": {
                "type": ["string", "null"],
                "enum": [*MODULES, None],
                "description": (
                    "Only set this if the question clearly names one of these "
                    "modules. Leave null if the question doesn't mention a "
                    "specific module or is ambiguous — never guess."
                ),
            },
        },
        "required": ["route"],
    },
}


class OrchestrationState(TypedDict, total=False):
    question: str
    route: Route
    module: str | None
    final_answer: str
    blocked: bool


def classify_question(
    client: anthropic.Anthropic, *, model: str, question: str
) -> tuple[Route, str | None]:
    """Tool-forced classification call. Pure function, independently testable
    with a mocked client — no graph access, no side effects."""
    response = client.messages.create(  # type: ignore[call-overload]
        model=model,
        max_tokens=256,
        system=(
            "You classify questions about a SAP ECC-to-S/4HANA GxP migration "
            "knowledge graph. Always call classify_question exactly once."
        ),
        messages=[{"role": "user", "content": question}],
        tools=[_CLASSIFY_TOOL],
        tool_choice={"type": "tool", "name": "classify_question"},
    )
    for block in response.content:
        if block.type == "tool_use":
            route = block.input["route"]
            if route not in ("as_is", "mapping", "compliance", "impact", "gaps", "temporal"):
                raise ValueError(f"model returned an unknown route: {route!r}")
            module = block.input.get("module")
            return cast(Route, route), (str(module) if module else None)
    raise ValueError("model did not return a tool_use classification block")


def _classify_node(
    client: anthropic.Anthropic, *, model: str, logger: structlog.stdlib.BoundLogger
) -> Any:
    def node(state: OrchestrationState) -> OrchestrationState:
        degraded: OrchestrationState = {
            "route": "error",
            "module": None,
            "final_answer": _CLASSIFICATION_UNAVAILABLE,
            "blocked": False,
        }
        try:
            route, module = classify_question(client, model=model, question=state["question"])
        except anthropic.AnthropicError as exc:
            logger.error(
                "agents.graph.classification_api_error",
                question=state["question"],
                error=str(exc),
            )
            return degraded
        except ValueError as exc:
            logger.error(
                "agents.graph.classification_malformed_response",
                question=state["question"],
                error=str(exc),
            )
            return degraded

        logger.info(
            "agents.graph.classified", question=state["question"], route=route, module=module
        )
        return {"route": route, "module": module}

    return node


def _error_node() -> Any:
    """Terminal node for a classification failure -- _classify_node already set
    final_answer/blocked on the state before routing here; this node is a pure
    passthrough to END, never overwriting what's already a safe degraded answer."""

    def node(state: OrchestrationState) -> OrchestrationState:
        return {}

    return node


def _as_is_node(
    client: anthropic.Anthropic,
    graph: Graph,
    *,
    model: str,
    schema_context: str,
    logger: structlog.stdlib.BoundLogger,
) -> Any:
    def node(state: OrchestrationState) -> OrchestrationState:
        agent = AsIsQueryAgent(
            client, graph, model=model, schema_context=schema_context, logger=logger
        )
        result = agent.ask(state["question"])
        return {"final_answer": result.answer, "blocked": result.blocked}

    return node


def _mapping_node(client: anthropic.Anthropic, graph: Graph, *, model: str) -> Any:
    def node(state: OrchestrationState) -> OrchestrationState:
        report = build_mapping_report(
            graph,
            module=state.get("module"),
            narrate=True,
            client=client,
            model=model,
            question=state["question"],
        )
        return {"final_answer": report.narrative or "", "blocked": False}

    return node


def _compliance_node(client: anthropic.Anthropic, graph: Graph, *, model: str) -> Any:
    def node(state: OrchestrationState) -> OrchestrationState:
        report = build_compliance_report(
            graph, narrate=True, client=client, model=model, question=state["question"]
        )
        return {"final_answer": report.narrative or "", "blocked": False}

    return node


def _impact_node(client: anthropic.Anthropic, graph: Graph, *, model: str) -> Any:
    def node(state: OrchestrationState) -> OrchestrationState:
        report = build_impact_report(graph, narrate=True, client=client, model=model)
        return {"final_answer": report.narrative or "", "blocked": False}

    return node


def _gaps_node(client: anthropic.Anthropic, graph: Graph, *, model: str) -> Any:
    def node(state: OrchestrationState) -> OrchestrationState:
        report = build_gaps_report(graph, narrate=True, client=client, model=model)
        return {"final_answer": report.narrative or "", "blocked": False}

    return node


def _temporal_node() -> Any:
    def node(state: OrchestrationState) -> OrchestrationState:
        return {"final_answer": answer_temporal_question(), "blocked": False}

    return node


def _select_route(state: OrchestrationState) -> Route:
    return state["route"]


def build_orchestration_graph(
    client: anthropic.Anthropic,
    graph: Graph,
    *,
    model: str,
    schema_context: str,
    logger: structlog.stdlib.BoundLogger,
) -> CompiledStateGraph[OrchestrationState, None, OrchestrationState, OrchestrationState]:
    """Wires classify -> {as_is, mapping, compliance, impact, gaps, temporal, error}
    -> END, keyed on state['route']. 'error' is never a route the model can choose
    (classify_question rejects it); it's set internally by _classify_node when
    Anthropic is unreachable or returns a malformed response, so the caller gets a
    clean degraded answer instead of an unhandled exception."""
    builder: StateGraph[OrchestrationState, None, OrchestrationState, OrchestrationState] = (
        StateGraph(OrchestrationState)
    )
    builder.add_node("classify", _classify_node(client, model=model, logger=logger))
    builder.add_node(
        "as_is",
        _as_is_node(client, graph, model=model, schema_context=schema_context, logger=logger),
    )
    builder.add_node("mapping", _mapping_node(client, graph, model=model))
    builder.add_node("compliance", _compliance_node(client, graph, model=model))
    builder.add_node("impact", _impact_node(client, graph, model=model))
    builder.add_node("gaps", _gaps_node(client, graph, model=model))
    builder.add_node("temporal", _temporal_node())
    builder.add_node("error", _error_node())

    builder.set_entry_point("classify")
    builder.add_conditional_edges(
        "classify",
        _select_route,
        {
            "as_is": "as_is",
            "mapping": "mapping",
            "compliance": "compliance",
            "impact": "impact",
            "gaps": "gaps",
            "temporal": "temporal",
            "error": "error",
        },
    )
    builder.add_edge("error", END)
    builder.add_edge("as_is", END)
    builder.add_edge("mapping", END)
    builder.add_edge("compliance", END)
    builder.add_edge("impact", END)
    builder.add_edge("gaps", END)
    builder.add_edge("temporal", END)

    return builder.compile()


def route_question(
    app: CompiledStateGraph[OrchestrationState, None, OrchestrationState, OrchestrationState],
    question: str,
) -> OrchestrationState:
    """Invokes the compiled graph and returns the final state (final_answer + blocked)."""
    return cast(OrchestrationState, app.invoke({"question": question}))
