"""NOTE: This file has been sanitized for public/private portfolio use.
Business logic, domain-specific rules, and proprietary details have been masked.
The coding patterns, architecture, and technical implementation remain authentic.
[MASKED] tags indicate where original business logic has been replaced.

Golden-question regression net for the orchestrated agent layer (Phase 3 §3.7).

MANUAL ONLY — never runs in CI (see the `eval` marker in pyproject.toml and the
`.github/workflows/ci.yml` note in docs/AUDIT.md). Unlike every other test in this
suite, these hit the REAL Anthropic API against the REAL, already-loaded FalkorDB
graph (`make up && make load` first) — that is the entire point: catching real
model/prompt regressions that a mocked test cannot see. Assertions are
substring/contains-style, never exact-match, since real model phrasing varies.

Run with: `make eval` (requires a real ANTHROPIC_API_KEY in .env and a loaded graph).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from falkordb import FalkorDB, Graph
from langgraph.graph.state import CompiledStateGraph

from kgme.agents.graph import OrchestrationState, build_orchestration_graph, route_question
from kgme.agents.llm_client import build_anthropic_client
from kgme.agents.schema_context import build_schema_context
from kgme.config import load_settings
from kgme.core.observability import get_logger
from kgme.db.driver import build_client, close_client, get_graph

pytestmark = pytest.mark.eval


@pytest.fixture(scope="module")
def real_graph() -> Iterator[Graph]:
    settings = load_settings()
    client: FalkorDB = build_client(settings)
    try:
        yield get_graph(client, settings=settings)
    finally:
        close_client(client)


OrchestrationApp = CompiledStateGraph[
    OrchestrationState, None, OrchestrationState, OrchestrationState
]


@pytest.fixture(scope="module")
def app(real_graph: Graph) -> OrchestrationApp:
    settings = load_settings()
    client = build_anthropic_client(settings)
    schema_context = build_schema_context(real_graph)
    return build_orchestration_graph(
        client,
        real_graph,
        model=settings.anthropic_model,
        schema_context=schema_context,
        logger=get_logger("eval"),
    )


GOLDEN_QUESTIONS = [
    (
        "What SAP transactions does business process MM01 use?",
        "as_is",
        ["MB1C", "documented"],
    ),
    ("Does the transaction MB1C exist in the graph?", "as_is", ["MB1C"]),
    ("What's the MIGRATES_TO coverage for MM?", "mapping", ["3", "12"]),
    (
        "What's mapped in AM?",
        "mapping",
        ["0", "57"],
    ),
    ("Show me the migration mapping coverage overall", "mapping", []),
    (
        "What are the GxP compliance risks in this migration?",
        "compliance",
        ["QM:BATCH_RELEASE", "SYS:LAB_SYSTEM"],
    ),
    # Reclassified from "compliance" to "gaps" once the dedicated gaps route existed
    # (see agents/gaps.py) — a plain inventory answer is more precise for these two
    # than compliance's GxP-risk-narrative framing; verified via manual /ask that the
    # "gaps" answer directly and completely addresses both questions.
    ("What compliance gaps exist in this graph?", "gaps", []),
    ("Which findings are only inferred, not documented?", "gaps", ["inferred"]),
    ("What roles authorize business process MM01?", "as_is", ["MM01"]),
    ("What's the coverage status for the AM module migration?", "mapping", ["AM"]),
    (
        "Is there a risk related to the Lab System and batch release?",
        "compliance",
        ["Lab System"],
    ),
    ("What SOP does process MM01 follow?", "as_is", ["MM01"]),
    (
        "How do the MM and AM modules connect to each other?",
        "impact",
        ["MM13", "MM18", "AM01"],
    ),
    (
        "Is there a cross-module reconciliation chain in this migration?",
        "impact",
        ["RECONCILES_TO"],
    ),
    (
        "What's the weakest link in the investment-to-asset reconciliation path?",
        "impact",
        ["inferred"],
    ),
    (
        "What documents are referenced in this graph but not actually in our possession?",
        "gaps",
        ["gap"],
    ),
    (
        "List the SOP and ReferencedDocument nodes in the graph that have gap confidence",
        "gaps",
        ["sop"],
    ),
    (
        "How old is the documentation behind business process MM01, and should it be "
        "treated as still valid today?",
        "temporal",
        ["2005", "2025"],
    ),
    (
        "What's MM's migration disposition, and is that documented fact or the analyst's "
        "own opinion?",
        "mapping",
        ["opinion"],
    ),
    # Regression test for a real found-and-fixed bug: schema_context.py's NODE
    # PROPERTIES list was hand-written, never queried live, so it silently missed
    # the s4_* properties enrichment/s4_simplification.py adds. Before the fix this
    # question got "I cannot provide it without inventing an answer" -- a false
    # negative, not an honest one, since the data genuinely exists on this node.
    # Substrings are paraphrase-tolerant (the model narrates confidence as
    # "[inferred]" prose, not the literal property name s4_confidence).
    (
        "What does the SAP Simplification Catalog say about TX:AS21?",
        "as_is",
        ["Deprecated", "inferred"],
    ),
    # Regression test for the compliance agent's S4-catalog extension: TX:AS21 has
    # zero relationships in this graph, so a path-based query could never surface
    # it -- only run_s4_catalog_scan()'s node-level query can.
    (
        "What compliance risks does the SAP Simplification Catalog reveal for the AM module?",
        "compliance",
        ["s4_confidence", "inferred"],
    ),
    # Regression test for a real routing bug found while writing the question
    # above: this exact neutral, inventory-style phrasing ("are there any
    # findings", no explicit risk language) originally misrouted to "gaps" --
    # which has no S4-catalog awareness -- instead of "compliance". Fixed by
    # making _CLASSIFY_TOOL's route description explicit that S4/Simplification
    # Catalog questions are always "compliance", regardless of risk-vs-inventory
    # phrasing.
    (
        "Are there any SAP Simplification Catalog findings for the AM module?",
        "compliance",
        [],
    ),
]


@pytest.mark.parametrize(("question", "expected_route", "expected_substrings"), GOLDEN_QUESTIONS)
def test_golden_question(
    app: OrchestrationApp, question: str, expected_route: str, expected_substrings: list[str]
) -> None:
    result = route_question(app, question)

    assert result["route"] == expected_route, (
        f"expected route {expected_route!r}, got {result['route']!r} for: {question!r}"
    )
    for substring in expected_substrings:
        assert substring.lower() in result["final_answer"].lower(), (
            f"expected {substring!r} in answer for {question!r}, got: {result['final_answer']!r}"
        )
