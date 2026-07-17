"""Unit tests for agents/impact.py — chain assembly and weakest-link ranking on
a mocked graph, mirroring tests/unit/test_mapping.py's mock style."""

from __future__ import annotations

from unittest.mock import MagicMock

from kgme.agents.impact import (
    ChainHop,
    ReconciliationChain,
    build_impact_report,
    compute_reconciliation_chains,
    narrate_impact_report,
)


def _chain_row() -> list[object]:
    return [
        "PROC:AM01",
        "documented",
        "AM",
        "LINKED_VIA_INVESTMENT",
        "documented",
        "PH_AM01",
        "PROC:MM13",
        "peripheral",
        "MM",
        "FOLLOWED_BY",
        "inferred",
        "PH_AM01",
        "PROC:MM18",
        "peripheral",
        "MM",
        "RECONCILES_TO",
        "documented",
        "PH_AM01",
    ]


def _chain() -> ReconciliationChain:
    return ReconciliationChain(
        start=ChainHop("PROC:AM01", "documented", "AM", 10, 8, 1),
        investment_relation="LINKED_VIA_INVESTMENT",
        investment_confidence="documented",
        investment_source_doc="PH_AM01",
        mid1=ChainHop("PROC:MM13", "peripheral", "MM", 1, 0, 1),
        intermediate_relation="FOLLOWED_BY",
        intermediate_confidence="inferred",
        intermediate_source_doc="PH_AM01",
        mid2=ChainHop("PROC:MM18", "peripheral", "MM", 2, 1, 0),
        reconciliation_relation="RECONCILES_TO",
        reconciliation_confidence="documented",
        reconciliation_source_doc="PH_AM01",
    )


def test_weakest_link_confidence_picks_the_inferred_hop() -> None:
    chain = _chain()

    assert chain.weakest_link_confidence == "inferred"


def test_weakest_link_confidence_none_when_no_confidence_known() -> None:
    chain = ReconciliationChain(
        start=ChainHop("PROC:AM01", None, "AM", 0, 0, 0),
        investment_relation="LINKED_VIA_INVESTMENT",
        investment_confidence=None,
        investment_source_doc=None,
        mid1=ChainHop("PROC:MM13", None, "MM", 0, 0, 0),
        intermediate_relation="FOLLOWED_BY",
        intermediate_confidence=None,
        intermediate_source_doc=None,
        mid2=ChainHop("PROC:MM18", None, "MM", 0, 0, 0),
        reconciliation_relation="RECONCILES_TO",
        reconciliation_confidence=None,
        reconciliation_source_doc=None,
    )

    assert chain.weakest_link_confidence is None


def test_compute_reconciliation_chains_assembles_from_rows() -> None:
    fake_graph = MagicMock()
    fake_graph.ro_query.side_effect = [
        MagicMock(result_set=[_chain_row()]),
        MagicMock(result_set=[[10, 8, 1]]),  # AM01 inbound counts
        MagicMock(result_set=[[1, 0, 1]]),  # MM13 inbound counts
        MagicMock(result_set=[[2, 1, 0]]),  # MM18 inbound counts
    ]

    chains = compute_reconciliation_chains(fake_graph)

    assert len(chains) == 1
    chain = chains[0]
    assert chain.start.node_id == "PROC:AM01"
    assert chain.mid1.node_id == "PROC:MM13"
    assert chain.mid2.node_id == "PROC:MM18"
    assert chain.mid1.confidence == "peripheral"
    assert chain.weakest_link_confidence == "inferred"


def test_compute_reconciliation_chains_returns_empty_list_when_none_found() -> None:
    fake_graph = MagicMock()
    fake_graph.ro_query.return_value.result_set = []

    assert compute_reconciliation_chains(fake_graph) == []


def test_narrate_impact_report_extracts_text_block() -> None:
    fake_client = MagicMock()
    fake_block = MagicMock(type="text", text="The FOLLOWED_BY hop is the weakest link.")
    fake_client.messages.create.return_value = MagicMock(content=[fake_block])

    narrative = narrate_impact_report(fake_client, model="claude-sonnet-5", chains=[_chain()])

    assert "weakest link" in narrative.lower()


def test_narrate_impact_report_handles_no_chains_without_a_real_client_call() -> None:
    fake_client = MagicMock()
    fake_client.messages.create.return_value = MagicMock(
        content=[MagicMock(type="text", text="No chain found.")]
    )

    narrative = narrate_impact_report(fake_client, model="claude-sonnet-5", chains=[])

    assert "no" in narrative.lower()
    prompt = fake_client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "No LINKED_VIA_INVESTMENT" in prompt


def test_build_impact_report_without_narration_has_no_narrative() -> None:
    fake_graph = MagicMock()
    fake_graph.ro_query.return_value.result_set = []

    report = build_impact_report(fake_graph, narrate=False)

    assert report.narrative is None
    assert report.chains == []


def test_build_impact_report_with_narration_requires_client() -> None:
    fake_graph = MagicMock()
    fake_graph.ro_query.return_value.result_set = []

    try:
        build_impact_report(fake_graph, narrate=True, client=None)
        raised = False
    except ValueError:
        raised = True
    assert raised
