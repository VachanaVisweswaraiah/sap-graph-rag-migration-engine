"""Unit tests for agents/graph.py — classify_question and each node function in
isolation, with mocked Anthropic client/graph. No real LangGraph invocation here
(that's covered by tests/integration/test_graph.py against a real graph)."""

from __future__ import annotations

from unittest.mock import MagicMock

import anthropic
import pytest

from kgme.agents.as_is import AsIsAnswer, AsIsQueryAgent
from kgme.agents.graph import (
    _CLASSIFICATION_UNAVAILABLE,
    _as_is_node,
    _classify_node,
    _compliance_node,
    _gaps_node,
    _impact_node,
    _mapping_node,
    _temporal_node,
    classify_question,
)
from kgme.core.observability import get_logger


def _tool_use(route: str, module: str | None = None) -> MagicMock:
    block = MagicMock(type="tool_use", input={"route": route, "module": module})
    return MagicMock(content=[block])


def test_classify_question_returns_as_is_route() -> None:
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _tool_use("as_is")

    route, module = classify_question(
        fake_client, model="claude-sonnet-5", question="What transactions does MM01 use?"
    )

    assert route == "as_is"
    assert module is None


def test_classify_question_returns_mapping_route_with_module() -> None:
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _tool_use("mapping", "MM")

    route, module = classify_question(
        fake_client, model="claude-sonnet-5", question="What's mapped in MM?"
    )

    assert route == "mapping"
    assert module == "MM"


def test_classify_question_returns_compliance_route() -> None:
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _tool_use("compliance")

    route, module = classify_question(
        fake_client, model="claude-sonnet-5", question="What are the GxP compliance risks?"
    )

    assert route == "compliance"
    assert module is None


def test_classify_question_returns_impact_route() -> None:
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _tool_use("impact")

    route, module = classify_question(
        fake_client,
        model="claude-sonnet-5",
        question="How do the MM and AM modules connect?",
    )

    assert route == "impact"
    assert module is None


def test_classify_question_returns_gaps_route() -> None:
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _tool_use("gaps")

    route, module = classify_question(
        fake_client,
        model="claude-sonnet-5",
        question="What documents are referenced but not in our possession?",
    )

    assert route == "gaps"
    assert module is None


def test_classify_question_returns_temporal_route() -> None:
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _tool_use("temporal")

    route, module = classify_question(
        fake_client,
        model="claude-sonnet-5",
        question="How old is this documentation, is it still valid?",
    )

    assert route == "temporal"
    assert module is None


def test_classify_question_leaves_module_none_when_ambiguous() -> None:
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _tool_use("mapping", None)

    _, module = classify_question(
        fake_client, model="claude-sonnet-5", question="Show me the migration coverage"
    )

    assert module is None


def test_classify_question_raises_on_unknown_route() -> None:
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _tool_use("not_a_real_route")

    with pytest.raises(ValueError, match="unknown route"):
        classify_question(fake_client, model="claude-sonnet-5", question="anything")


def test_classify_node_degrades_safely_on_anthropic_api_error() -> None:
    """Regression test: unlike every other Anthropic call site in this codebase
    (as_is.py's two calls), the classification node used to have zero exception
    handling -- a transient Anthropic outage propagated as an unhandled exception
    out of /ask instead of a clean degraded answer."""
    fake_client = MagicMock()
    fake_client.messages.create.side_effect = anthropic.AnthropicError("simulated outage")

    node = _classify_node(fake_client, model="claude-sonnet-5", logger=get_logger("t"))
    result = node({"question": "What does MM01 use?"})

    assert result["route"] == "error"
    assert result["module"] is None
    assert result["final_answer"] == _CLASSIFICATION_UNAVAILABLE
    assert result["blocked"] is False


def test_classify_node_degrades_safely_on_malformed_model_response() -> None:
    """Same degraded path for a model response that doesn't classify at all
    (no tool_use block) or names a route outside the enum."""
    fake_client = MagicMock()
    fake_client.messages.create.return_value = MagicMock(content=[])  # no tool_use block

    node = _classify_node(fake_client, model="claude-sonnet-5", logger=get_logger("t"))
    result = node({"question": "What does MM01 use?"})

    assert result["route"] == "error"
    assert result["final_answer"] == _CLASSIFICATION_UNAVAILABLE
    assert result["blocked"] is False


def test_as_is_node_delegates_to_agent_ask(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = MagicMock()
    fake_graph = MagicMock()
    answer = AsIsAnswer(
        question="q", cypher="MATCH (n) RETURN n", answer="the answer", blocked=False
    )
    monkeypatch.setattr(AsIsQueryAgent, "ask", lambda self, question: answer)

    node = _as_is_node(
        fake_client, fake_graph, model="claude-sonnet-5", schema_context="", logger=get_logger("t")
    )
    result = node({"question": "some question"})

    assert result["final_answer"] == "the answer"
    assert result["blocked"] is False


def test_mapping_node_uses_module_from_state() -> None:
    fake_client = MagicMock()
    fake_graph = MagicMock()
    fake_graph.ro_query.return_value.result_set = [[12, 3, [["TX:A", "TX:B"]]]]
    fake_client.messages.create.return_value = MagicMock(
        content=[MagicMock(type="text", text="mapping narrative")]
    )

    node = _mapping_node(fake_client, fake_graph, model="claude-sonnet-5")
    result = node({"question": "q", "module": "MM"})

    assert result["final_answer"] == "mapping narrative"
    call_kwargs = fake_graph.ro_query.call_args
    assert call_kwargs[0][1] == {"module": "MM"}


def test_compliance_node_returns_narrative() -> None:
    fake_client = MagicMock()
    fake_graph = MagicMock()
    fake_graph.ro_query.return_value.result_set = []
    fake_client.messages.create.return_value = MagicMock(
        content=[MagicMock(type="text", text="compliance narrative")]
    )

    node = _compliance_node(fake_client, fake_graph, model="claude-sonnet-5")
    result = node({"question": "q"})

    assert result["final_answer"] == "compliance narrative"
    assert result["blocked"] is False


def test_impact_node_returns_narrative() -> None:
    fake_client = MagicMock()
    fake_graph = MagicMock()
    fake_graph.ro_query.return_value.result_set = []
    fake_client.messages.create.return_value = MagicMock(
        content=[MagicMock(type="text", text="impact narrative")]
    )

    node = _impact_node(fake_client, fake_graph, model="claude-sonnet-5")
    result = node({"question": "q"})

    assert result["final_answer"] == "impact narrative"
    assert result["blocked"] is False


def test_gaps_node_returns_narrative() -> None:
    fake_client = MagicMock()
    fake_graph = MagicMock()
    fake_graph.ro_query.return_value.result_set = []
    fake_client.messages.create.return_value = MagicMock(
        content=[MagicMock(type="text", text="gaps narrative")]
    )

    node = _gaps_node(fake_client, fake_graph, model="claude-sonnet-5")
    result = node({"question": "q"})

    assert result["final_answer"] == "gaps narrative"
    assert result["blocked"] is False


def test_temporal_node_returns_the_fixed_answer_without_any_client_or_graph_call() -> None:
    node = _temporal_node()
    result = node({"question": "q"})

    assert "does not track document dates" in result["final_answer"]
    assert result["blocked"] is False


def test_mapping_node_reports_zero_coverage_module_honestly() -> None:
    fake_client = MagicMock()
    fake_graph = MagicMock()
    fake_graph.ro_query.return_value.result_set = [[57, 0, [["TX:AS01", None]]]]
    fake_client.messages.create.return_value = MagicMock(
        content=[MagicMock(type="text", text="AM has 0/57 mapped")]
    )

    node = _mapping_node(fake_client, fake_graph, model="claude-sonnet-5")
    result = node({"question": "q", "module": "AM"})

    assert result["final_answer"] == "AM has 0/57 mapped"
