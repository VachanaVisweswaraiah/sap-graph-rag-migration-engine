"""NOTE: This file has been sanitized for public/private portfolio use.
Business logic, domain-specific rules, and proprietary details have been masked.
The coding patterns, architecture, and technical implementation remain authentic.
[MASKED] tags indicate where original business logic has been replaced.

Unit tests for agents/mapping.py — pure logic (dataclass shape / narration
gating), no DB, no real LLM call."""

from __future__ import annotations

from unittest.mock import MagicMock

from kgme.agents.mapping import ModuleCoverage, build_mapping_report, narrate_mapping_report


def _coverage() -> list[ModuleCoverage]:
    return [
        ModuleCoverage(
            module="MM",
            total_transactions=12,
            mapped_transactions=3,
            mapped_pairs=[("TX:MB1C", "TX:MIGO")],
        ),
        ModuleCoverage(module="AM", total_transactions=57, mapped_transactions=0, mapped_pairs=[]),
    ]


def test_narrate_mapping_report_extracts_text_block() -> None:
    fake_client = MagicMock()
    fake_block = MagicMock(type="text", text="MM is partially mapped; AM has no mappings yet.")
    fake_client.messages.create.return_value = MagicMock(content=[fake_block])

    narrative = narrate_mapping_report(fake_client, model="claude-sonnet-5", coverage=_coverage())

    assert "AM has no mappings yet" in narrative


def test_narrate_mapping_report_prompt_states_disposition_is_opinion_not_fact() -> None:
    fake_client = MagicMock()
    fake_client.messages.create.return_value = MagicMock(
        content=[MagicMock(type="text", text="narrative")]
    )

    narrate_mapping_report(fake_client, model="claude-sonnet-5", coverage=_coverage())

    system_prompt = fake_client.messages.create.call_args.kwargs["system"]
    assert "opinion" in system_prompt
    assert "inferred migration call" in system_prompt


def test_build_mapping_report_without_narration_has_no_narrative() -> None:
    fake_graph = MagicMock()
    fake_graph.ro_query.return_value.result_set = [[12, 3, [["TX:MB1C", "TX:MIGO"]]]]

    report = build_mapping_report(fake_graph, module="MM", narrate=False)

    assert report.narrative is None
    assert report.coverage[0].module == "MM"


def test_build_mapping_report_with_narration_requires_client() -> None:
    fake_graph = MagicMock()
    fake_graph.ro_query.return_value.result_set = [[12, 3, [["TX:MB1C", "TX:MIGO"]]]]

    try:
        build_mapping_report(fake_graph, module="MM", narrate=True, client=None)
        raised = False
    except ValueError:
        raised = True
    assert raised


def test_build_mapping_report_zero_coverage_module_is_reported_honestly() -> None:
    fake_graph = MagicMock()
    fake_graph.ro_query.return_value.result_set = [[57, 0, [["TX:AS01", None]]]]

    report = build_mapping_report(fake_graph, module="AM", narrate=False)

    assert report.coverage[0].mapped_transactions == 0
    assert report.coverage[0].mapped_pairs == []
