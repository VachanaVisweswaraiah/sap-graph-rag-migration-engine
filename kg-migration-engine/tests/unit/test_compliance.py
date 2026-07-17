"""NOTE: This file has been sanitized for public/private portfolio use.
Business logic, domain-specific rules, and proprietary details have been masked.
The coding patterns, architecture, and technical implementation remain authentic.
[MASKED] tags indicate where original business logic has been replaced.

Unit tests for agents/compliance.py — severity tier ranking and flagship-pin
logic on hand-built ComplianceFinding lists. Pure logic, no DB, no LLM."""

from __future__ import annotations

from unittest.mock import MagicMock

from kgme.agents.compliance import (
    ComplianceFinding,
    S4CatalogFinding,
    build_compliance_report,
    narrate_compliance_report,
    run_compliance_scan,
    run_s4_catalog_scan,
)


def _finding(
    source_id: str,
    relation: str,
    target_id: str,
    *,
    edge_confidence: str | None,
    target_gxp: str | None,
    is_flagship: bool = False,
) -> ComplianceFinding:
    return ComplianceFinding(
        source_id=source_id,
        relation=relation,
        target_id=target_id,
        edge_confidence=edge_confidence,
        target_gxp=target_gxp,
        source_doc="TEST_FIXTURE",
        is_flagship=is_flagship,
    )


def test_narrate_compliance_report_extracts_text_block() -> None:
    fake_client = MagicMock()
    fake_block = MagicMock(type="text", text="The flagship finding is the top risk.")
    fake_client.messages.create.return_value = MagicMock(content=[fake_block])
    findings = [
        _finding(
            "QM:BATCH_RELEASE",
            "SUSPECTED_SOURCE",
            "SYS:LAB_SYSTEM",
            edge_confidence="inferred",
            target_gxp="",
            is_flagship=True,
        )
    ]

    narrative = narrate_compliance_report(fake_client, model="claude-sonnet-5", findings=findings)

    assert "flagship" in narrative.lower()


def test_build_compliance_report_without_narration_has_no_narrative() -> None:
    fake_graph = MagicMock()
    fake_graph.ro_query.return_value.result_set = []

    report = build_compliance_report(fake_graph, narrate=False)

    assert report.narrative is None
    assert report.findings == []
    assert report.s4_findings == []


def test_build_compliance_report_with_narration_requires_client() -> None:
    fake_graph = MagicMock()
    fake_graph.ro_query.return_value.result_set = []

    try:
        build_compliance_report(fake_graph, narrate=True, client=None)
        raised = False
    except ValueError:
        raised = True
    assert raised


def test_run_compliance_scan_pins_flagship_first_even_when_returned_last() -> None:
    fake_graph = MagicMock()
    fake_graph.ro_query.return_value.result_set = [
        ["ROLE:X", "AUTHORIZES", "PROC:MM01", "documented", "GxP-kritisch", "DOC:A"],
        ["DOC:GAP1", "PRECEDES", "DOC:GAP2", "documented", "", "TEST_FIXTURE"],
        ["TX:A", "SUSPECTED_USES_BWA", "TX:B", "inferred", "", "DERIVED:x"],
        # Flagship pattern deliberately listed last.
        ["QM:BATCH_RELEASE", "SUSPECTED_SOURCE", "SYS:LAB_SYSTEM", "inferred", "", "DOC:RA_PROC01"],
    ]

    findings = run_compliance_scan(fake_graph)

    assert findings[0].is_flagship is True
    assert findings[0].source_id == "QM:BATCH_RELEASE"
    # Remaining findings still ordered by severity tier: inferred edge before
    # documented-GxP-kritisch context, and gap-touching path is unaffected by
    # target_gxp since it's neither inferred nor GxP-kritisch-targeted.
    assert findings[1].source_id == "TX:A"


def test_run_compliance_scan_returns_empty_list_when_no_findings() -> None:
    fake_graph = MagicMock()
    fake_graph.ro_query.return_value.result_set = []

    assert run_compliance_scan(fake_graph) == []


def test_run_s4_catalog_scan_finds_isolated_nodes() -> None:
    """The whole point of this being a separate, node-level query: it must find
    a node with zero relationships, which run_compliance_scan's path-based query
    structurally cannot (verified live: TX:AS21 has none)."""
    fake_graph = MagicMock()
    fake_graph.ro_query.return_value.result_set = [
        [
            "TX:AS21",
            "Anlagenkomplex anlegen",
            "AM",
            "Deprecated",
            "Functional Gap (Process will break)",
            "None (Compatibility Scope ID 428)",
            "inferred",
            "DERIVED:SAP_SIMPLIFICATION_LIST",
        ]
    ]

    findings = run_s4_catalog_scan(fake_graph)

    assert findings == [
        S4CatalogFinding(
            node_id="TX:AS21",
            label="Anlagenkomplex anlegen",
            module="AM",
            s4_status="Deprecated",
            s4_severity="Functional Gap (Process will break)",
            s4_target="None (Compatibility Scope ID 428)",
            s4_confidence="inferred",
            s4_source_doc="DERIVED:SAP_SIMPLIFICATION_LIST",
        )
    ]


def test_run_s4_catalog_scan_returns_empty_list_when_none_flagged() -> None:
    fake_graph = MagicMock()
    fake_graph.ro_query.return_value.result_set = []

    assert run_s4_catalog_scan(fake_graph) == []


def test_build_compliance_report_includes_s4_findings() -> None:
    fake_graph = MagicMock()
    fake_graph.ro_query.side_effect = [
        MagicMock(result_set=[]),  # run_compliance_scan
        MagicMock(
            result_set=[
                [
                    "TX:AS21",
                    "label",
                    "AM",
                    "Deprecated",
                    "Functional Gap",
                    "None",
                    "inferred",
                    "doc",
                ]
            ]
        ),  # run_s4_catalog_scan
    ]

    report = build_compliance_report(fake_graph, narrate=False)

    assert report.s4_findings[0].node_id == "TX:AS21"


def test_narrate_compliance_report_covers_s4_catalog_findings_as_a_separate_section() -> None:
    """s4_severity/s4_status must never be stated without their s4_confidence
    sibling -- same discipline the base confidence/source_doc fields already get."""
    fake_client = MagicMock()
    fake_client.messages.create.return_value = MagicMock(
        content=[MagicMock(type="text", text="narrative")]
    )
    s4_findings = [
        S4CatalogFinding(
            node_id="TX:AS21",
            label="Anlagenkomplex anlegen",
            module="AM",
            s4_status="Deprecated",
            s4_severity="Functional Gap (Process will break)",
            s4_target=None,
            s4_confidence="inferred",
            s4_source_doc="DERIVED:SAP_SIMPLIFICATION_LIST",
        )
    ]

    narrate_compliance_report(
        fake_client, model="claude-sonnet-5", findings=[], s4_findings=s4_findings
    )

    system_prompt = fake_client.messages.create.call_args.kwargs["system"]
    user_content = fake_client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "S4-CATALOG" in system_prompt
    assert "separate risk category" in system_prompt.lower()
    assert "s4_confidence" in system_prompt
    assert "TX:AS21" in user_content
    assert "s4_confidence='inferred'" in user_content


def test_narrate_compliance_report_handles_no_s4_findings() -> None:
    """Must not crash or render an empty/malformed section when the s4-catalog
    enrichment hasn't been run yet -- the common case until someone runs it."""
    fake_client = MagicMock()
    fake_client.messages.create.return_value = MagicMock(
        content=[MagicMock(type="text", text="narrative")]
    )

    narrate_compliance_report(fake_client, model="claude-sonnet-5", findings=[])

    user_content = fake_client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "S/4 catalog findings" in user_content
    assert "(none)" in user_content
