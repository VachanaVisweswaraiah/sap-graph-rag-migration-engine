"""NOTE: This file has been sanitized for public/private portfolio use.
Business logic, domain-specific rules, and proprietary details have been masked.
The coding patterns, architecture, and technical implementation remain authentic.
[MASKED] tags indicate where original business logic has been replaced.

Unit tests for cli.py's exit-code logic — mocked driver/loader/health, no Docker.

Covers the two distinct failure modes main_load must handle (v2 fix): a raised
LoadAbortedError/SchemaViolationError (the run never returns a StepResult list),
and a normal return where some StepResult.ok is False.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import kgme.cli as cli_module
from kgme.agents.as_is import AsIsAnswer, AsIsQueryAgent
from kgme.agents.compliance import ComplianceFinding, ComplianceReport
from kgme.agents.mapping import MappingReport, ModuleCoverage
from kgme.core.exceptions import LoadAbortedError
from kgme.db.health import CheckResult, HealthReport
from kgme.db.loader import StepResult
from kgme.enrichment.disposition import DispositionSummary
from kgme.enrichment.s4_simplification import EnrichmentSummary


def _patch_common(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    fake_settings = MagicMock()
    fake_client = MagicMock()
    monkeypatch.setattr(cli_module, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(cli_module, "build_client", lambda settings: fake_client)
    monkeypatch.setattr(cli_module, "close_client", lambda client: None)
    return fake_client


def test_main_load_exits_zero_on_full_success(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_common(monkeypatch)
    results = [StepResult(step="verify", ok=True, summary={"nodes_ok": True}, duration_ms=1.0)]
    monkeypatch.setattr(cli_module, "load_graph", lambda *a, **k: results)

    cli_module.main_load([])  # must not raise / call sys.exit


def test_main_load_exits_one_on_load_aborted_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_common(monkeypatch)

    def raise_aborted(*args: object, **kwargs: object) -> list[StepResult]:
        raise LoadAbortedError("simulated abort")

    monkeypatch.setattr(cli_module, "load_graph", raise_aborted)

    with pytest.raises(SystemExit) as exc_info:
        cli_module.main_load([])
    assert exc_info.value.code == 1


def test_main_load_exits_one_when_step_result_not_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """The naive 'did it raise' check alone would miss this: load_graph returns
    normally, but 05_verify's assertions failed."""
    _patch_common(monkeypatch)
    results = [StepResult(step="verify", ok=False, summary={"nodes_ok": False}, duration_ms=1.0)]
    monkeypatch.setattr(cli_module, "load_graph", lambda *a, **k: results)

    with pytest.raises(SystemExit) as exc_info:
        cli_module.main_load([])
    assert exc_info.value.code == 1


def test_main_load_passes_wipe_flag_through(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_common(monkeypatch)
    captured: dict[str, object] = {}

    def fake_load_graph(*args: object, **kwargs: object) -> list[StepResult]:
        captured.update(kwargs)
        return [StepResult(step="verify", ok=True, summary={}, duration_ms=1.0)]

    monkeypatch.setattr(cli_module, "load_graph", fake_load_graph)

    cli_module.main_load(["--wipe"])

    assert captured["wipe"] is True


def test_db_check_exits_zero_when_healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_common(monkeypatch)
    report = HealthReport(
        healthy=True,
        checks=[CheckResult(name="connectivity", ok=True, detail="ok", duration_ms=1.0)],
    )
    monkeypatch.setattr(cli_module, "run_health_checks", lambda *a, **k: report)

    cli_module.main(["db", "check"])  # must not raise / call sys.exit


def test_db_check_exits_one_when_unhealthy(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_common(monkeypatch)
    report = HealthReport(
        healthy=False,
        checks=[CheckResult(name="constraints", ok=False, detail="missing", duration_ms=1.0)],
    )
    monkeypatch.setattr(cli_module, "run_health_checks", lambda *a, **k: report)

    with pytest.raises(SystemExit) as exc_info:
        cli_module.main(["db", "check", "--deep"])
    assert exc_info.value.code == 1


def test_enrich_s4_catalog_runs_and_exits_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_common(monkeypatch)
    monkeypatch.setattr(cli_module, "get_graph", lambda client, settings: MagicMock())
    monkeypatch.setattr(cli_module, "load_catalog", lambda path: [])
    summary = EnrichmentSummary(matched_count=3, unmatched=[], skipped=[])
    monkeypatch.setattr(cli_module, "enrich_graph", lambda graph, rows, logger: summary)

    cli_module.main(["enrich", "s4-catalog"])  # must not raise / call sys.exit


def test_enrich_s4_catalog_passes_file_flag_through(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_common(monkeypatch)
    monkeypatch.setattr(cli_module, "get_graph", lambda client, settings: MagicMock())
    captured: dict[str, object] = {}

    def fake_load_catalog(path: object) -> list[object]:
        captured["path"] = path
        return []

    monkeypatch.setattr(cli_module, "load_catalog", fake_load_catalog)
    summary = EnrichmentSummary(matched_count=0, unmatched=[], skipped=[])
    monkeypatch.setattr(cli_module, "enrich_graph", lambda graph, rows, logger: summary)

    cli_module.main(["enrich", "s4-catalog", "--file", "some/custom/path.json"])

    assert str(captured["path"]) == "some/custom/path.json"


def test_enrich_disposition_runs_and_exits_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_common(monkeypatch)
    monkeypatch.setattr(cli_module, "get_graph", lambda client, settings: MagicMock())
    monkeypatch.setattr(cli_module, "load_dispositions", lambda nodes, process_master: ([], []))
    summary = DispositionSummary(edges_written=3, properties_written=9, unmatched_targets=[])
    monkeypatch.setattr(cli_module, "apply_dispositions", lambda graph, facts, logger: summary)

    cli_module.main(["enrich", "disposition"])  # must not raise / call sys.exit


def test_enrich_disposition_passes_file_flags_through(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_common(monkeypatch)
    monkeypatch.setattr(cli_module, "get_graph", lambda client, settings: MagicMock())
    captured: dict[str, object] = {}

    def fake_load_dispositions(
        nodes_file: object, process_master_file: object
    ) -> tuple[list[object], list[object]]:
        captured["nodes_file"] = nodes_file
        captured["process_master_file"] = process_master_file
        return [], []

    monkeypatch.setattr(cli_module, "load_dispositions", fake_load_dispositions)
    summary = DispositionSummary(edges_written=0, properties_written=0, unmatched_targets=[])
    monkeypatch.setattr(cli_module, "apply_dispositions", lambda graph, facts, logger: summary)

    cli_module.main(
        [
            "enrich",
            "disposition",
            "--nodes-file",
            "custom/nodes.csv",
            "--process-master-file",
            "custom/master.csv",
        ]
    )

    assert str(captured["nodes_file"]) == "custom/nodes.csv"
    assert str(captured["process_master_file"]) == "custom/master.csv"


def _patch_ask_common(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_common(monkeypatch)
    monkeypatch.setattr(cli_module, "build_anthropic_client", lambda settings: MagicMock())
    monkeypatch.setattr(cli_module, "get_graph", lambda client, settings: MagicMock())
    monkeypatch.setattr(cli_module, "build_schema_context", lambda graph: "schema")


def test_ask_exits_zero_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_ask_common(monkeypatch)
    answer = AsIsAnswer(
        question="q", cypher="MATCH (n) RETURN n", answer="the answer", blocked=False
    )
    monkeypatch.setattr(AsIsQueryAgent, "ask", lambda self, question: answer)

    cli_module.main(["ask", "some question"])  # must not raise / call sys.exit


def test_ask_exits_one_when_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_ask_common(monkeypatch)
    answer = AsIsAnswer(question="q", cypher=None, answer="blocked", blocked=True)
    monkeypatch.setattr(AsIsQueryAgent, "ask", lambda self, question: answer)

    with pytest.raises(SystemExit) as exc_info:
        cli_module.main(["ask", "some question"])
    assert exc_info.value.code == 1


def test_ask_show_cypher_flag(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_ask_common(monkeypatch)
    answer = AsIsAnswer(
        question="q", cypher="MATCH (n) RETURN n", answer="the answer", blocked=False
    )
    monkeypatch.setattr(AsIsQueryAgent, "ask", lambda self, question: answer)

    cli_module.main(["ask", "some question", "--show-cypher"])

    captured = capsys.readouterr()
    assert "MATCH (n) RETURN n" in captured.out


def _patch_graph_only_common(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_common(monkeypatch)
    monkeypatch.setattr(cli_module, "build_anthropic_client", lambda settings: MagicMock())
    monkeypatch.setattr(cli_module, "get_graph", lambda client, settings: MagicMock())


def _patch_route_common(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_graph_only_common(monkeypatch)
    monkeypatch.setattr(cli_module, "build_schema_context", lambda graph: "schema")
    monkeypatch.setattr(cli_module, "build_orchestration_graph", lambda *a, **k: MagicMock())


def test_route_exits_zero_on_success(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_route_common(monkeypatch)
    monkeypatch.setattr(
        cli_module,
        "route_question",
        lambda app, question: {
            "route": "mapping",
            "final_answer": "MM: 3/12 mapped",
            "blocked": False,
        },
    )

    cli_module.main(["route", "what is mapped in MM"])  # must not raise / call sys.exit

    captured = capsys.readouterr()
    assert "[route: mapping]" in captured.out
    assert "MM: 3/12 mapped" in captured.out


def test_route_exits_one_when_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_route_common(monkeypatch)
    monkeypatch.setattr(
        cli_module,
        "route_question",
        lambda app, question: {"route": "as_is", "final_answer": "blocked", "blocked": True},
    )

    with pytest.raises(SystemExit) as exc_info:
        cli_module.main(["route", "some question"])
    assert exc_info.value.code == 1


def test_map_prints_coverage(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_graph_only_common(monkeypatch)
    coverage = [
        ModuleCoverage(
            module="MM",
            total_transactions=12,
            mapped_transactions=3,
            mapped_pairs=[("TX:MB1C", "TX:MIGO")],
        ),
        ModuleCoverage(module="AM", total_transactions=57, mapped_transactions=0, mapped_pairs=[]),
    ]
    monkeypatch.setattr(
        cli_module,
        "build_mapping_report",
        lambda graph, **kwargs: MappingReport(coverage=coverage, narrative=None),
    )

    cli_module.main(["map"])  # must not raise / call sys.exit

    captured = capsys.readouterr()
    assert "[MM] 3/12 mapped" in captured.out
    assert "TX:MB1C -> TX:MIGO" in captured.out
    assert "[AM] 0/57 mapped" in captured.out


def test_map_narrate_flag_prints_narrative(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_graph_only_common(monkeypatch)
    coverage = [
        ModuleCoverage(module="MM", total_transactions=12, mapped_transactions=3, mapped_pairs=[])
    ]
    monkeypatch.setattr(
        cli_module,
        "build_mapping_report",
        lambda graph, **kwargs: MappingReport(
            coverage=coverage, narrative="MM is partially mapped."
        ),
    )

    cli_module.main(["map", "--narrate"])

    captured = capsys.readouterr()
    assert "MM is partially mapped." in captured.out


def test_compliance_scan_prints_flagship_first(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_graph_only_common(monkeypatch)
    findings = [
        ComplianceFinding(
            source_id="QM:BATCH_RELEASE",
            relation="SUSPECTED_SOURCE",
            target_id="SYS:LAB_SYSTEM",
            edge_confidence="inferred",
            target_gxp="",
            source_doc="DOC:RA_PROC01",
            is_flagship=True,
        )
    ]
    monkeypatch.setattr(
        cli_module,
        "build_compliance_report",
        lambda graph, **kwargs: ComplianceReport(findings=findings, s4_findings=[], narrative=None),
    )

    cli_module.main(["compliance-scan"])  # must not raise / call sys.exit

    captured = capsys.readouterr()
    assert "[FLAGSHIP] QM:BATCH_RELEASE" in captured.out
