"""NOTE: This file has been sanitized for public/private portfolio use.
Business logic, domain-specific rules, and proprietary details have been masked.
The coding patterns, architecture, and technical implementation remain authentic.
[MASKED] tags indicate where original business logic has been replaced.

Unit tests for agents/gaps.py — the NL-narration wrapper around db/gaps.py's
fixed listing (distinct from tests/unit/test_gaps.py, which tests the db layer
directly). Mirrors tests/unit/test_mapping.py's mock style."""

from __future__ import annotations

from unittest.mock import MagicMock

from kgme.agents.gaps import build_gaps_report, narrate_gaps_report
from kgme.db.gaps import GapNode, InferredEdge


def _gap_nodes() -> list[GapNode]:
    return [
        GapNode(
            node_id="SOP:WI-000008",
            node_type="SOP",
            module="governance",
            source_doc="ZX_SAMPLE_01",
        )
    ]


def _inferred_edges() -> list[InferredEdge]:
    return [
        InferredEdge(
            source_id="QM:BATCH_RELEASE",
            relation="SUSPECTED_SOURCE",
            target_id="SYS:LAB_SYSTEM",
            source_doc="RA_PROC01",
        )
    ]


def test_narrate_gaps_report_extracts_text_block() -> None:
    fake_client = MagicMock()
    fake_block = MagicMock(type="text", text="These are retrieval targets, not settled facts.")
    fake_client.messages.create.return_value = MagicMock(content=[fake_block])

    narrative = narrate_gaps_report(
        fake_client,
        model="claude-sonnet-5",
        gap_nodes=_gap_nodes(),
        inferred_edges=_inferred_edges(),
    )

    assert "retrieval targets" in narrative


def test_narrate_gaps_report_states_empty_lists_plainly() -> None:
    fake_client = MagicMock()
    fake_client.messages.create.return_value = MagicMock(
        content=[MagicMock(type="text", text="none")]
    )

    narrate_gaps_report(fake_client, model="claude-sonnet-5", gap_nodes=[], inferred_edges=[])

    prompt = fake_client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "Gap nodes (0)" in prompt
    assert "(none)" in prompt


def test_build_gaps_report_without_narration_has_no_narrative() -> None:
    fake_graph = MagicMock()
    fake_graph.ro_query.return_value.result_set = []

    report = build_gaps_report(fake_graph, narrate=False)

    assert report.narrative is None
    assert report.gap_nodes == []
    assert report.inferred_edges == []


def test_build_gaps_report_with_narration_requires_client() -> None:
    fake_graph = MagicMock()
    fake_graph.ro_query.return_value.result_set = []

    try:
        build_gaps_report(fake_graph, narrate=True, client=None)
        raised = False
    except ValueError:
        raised = True
    assert raised
