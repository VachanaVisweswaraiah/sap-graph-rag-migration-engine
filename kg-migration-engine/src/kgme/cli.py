"""Console-script entry points: `kgme-load` and `kgme db check`.

NOTE: This file has been sanitized for public/private portfolio use.
Business logic, domain-specific rules, and proprietary details have been masked.
The coding patterns, architecture, and technical implementation remain authentic.
[MASKED] tags indicate where original business logic has been replaced.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from kgme.agents.as_is import AsIsQueryAgent
from kgme.agents.compliance import ComplianceReport, build_compliance_report
from kgme.agents.graph import build_orchestration_graph, route_question
from kgme.agents.llm_client import build_anthropic_client
from kgme.agents.mapping import MappingReport, build_mapping_report
from kgme.agents.schema_context import build_schema_context
from kgme.config import load_settings
from kgme.core.exceptions import KgmeError
from kgme.core.observability import get_logger
from kgme.db.driver import build_client, close_client, get_graph
from kgme.db.health import HealthReport, run_health_checks
from kgme.db.loader import StepResult, load_graph
from kgme.enrichment.disposition import DispositionSummary, apply_dispositions, load_dispositions
from kgme.enrichment.s4_simplification import EnrichmentSummary, enrich_graph, load_catalog

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_NODES_PATH = REPO_ROOT / "data" / "raw" / "kg_nodes.csv"
DEFAULT_EDGES_PATH = REPO_ROOT / "data" / "raw" / "kg_edges.csv"
DEFAULT_DATA_DICTIONARY_PATH = REPO_ROOT / "data" / "raw" / "kg_data_dictionary.csv"
DEFAULT_S4_CATALOG_PATH = REPO_ROOT / "data" / "external" / "s4hana_simplification_list.json"
DEFAULT_PROCESS_MASTER_PATH = REPO_ROOT / "data" / "raw" / "kg_process_master.csv"


def _print_step_results(results: list[StepResult]) -> None:
    for r in results:
        status = "OK" if r.ok else "FAILED"
        print(f"[{status}] {r.step} ({r.duration_ms:.1f}ms): {r.summary}")


def _print_health_report(report: HealthReport) -> None:
    for check in report.checks:
        status = "OK" if check.ok else "FAILED"
        print(f"[{status}] {check.name} ({check.duration_ms:.1f}ms): {check.detail}")


def main_load(argv: list[str] | None = None) -> None:
    """`kgme-load [--wipe]`. Exit 1 on either failure mode: a raised
    LoadAbortedError/SchemaViolationError (the run never produced a StepResult
    list), or a normal return where any StepResult.ok is False (e.g. 05_verify's
    assertions failed without raising) — a naive "any StepResult False" check
    alone would miss the first case."""
    parser = argparse.ArgumentParser(prog="kgme-load")
    parser.add_argument(
        "--wipe", action="store_true", help="DEV ONLY: wipe the graph before loading"
    )
    args = parser.parse_args(argv)

    logger = get_logger("cli")
    settings = load_settings()
    client = build_client(settings)
    try:
        results = load_graph(
            client,
            settings=settings,
            nodes_path=DEFAULT_NODES_PATH,
            edges_path=DEFAULT_EDGES_PATH,
            data_dictionary_path=DEFAULT_DATA_DICTIONARY_PATH,
            wipe=args.wipe,
        )
    except KgmeError as exc:
        logger.error("cli.load.failed", error=str(exc))
        print(f"Load aborted: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        close_client(client)

    _print_step_results(results)
    if any(not r.ok for r in results):
        sys.exit(1)


def _print_enrichment_summary(summary: EnrichmentSummary) -> None:
    print(f"[OK] matched {summary.matched_count} node(s) with S4 catalog properties")
    print(f"[INFO] {len(summary.unmatched)} catalog code(s) had no matching node")
    print(
        f"[INFO] {len(summary.skipped)} catalog row(s) skipped "
        "(Program/Concept — no corresponding node type in this graph yet)"
    )


def _enrich_s4_catalog(*, file: Path) -> None:
    settings = load_settings()
    client = build_client(settings)
    try:
        graph = get_graph(client, settings=settings)
        rows = load_catalog(file)
        summary = enrich_graph(graph, rows, get_logger("cli"))
    finally:
        close_client(client)

    _print_enrichment_summary(summary)


def _print_disposition_summary(summary: DispositionSummary, unparsed_count: int) -> None:
    print(f"[OK] {summary.edges_written} MIGRATES_TO edge(s) written")
    print(f"[OK] {summary.properties_written} node(s) received disposition_* properties")
    print(f"[INFO] {len(summary.unmatched_targets)} fact(s) had a missing source/target node")
    print(
        f"[INFO] {unparsed_count} disposition/notes cell(s) were unparseable (logged, not guessed)"
    )


def _enrich_disposition(*, nodes_file: Path, process_master_file: Path) -> None:
    settings = load_settings()
    client = build_client(settings)
    try:
        graph = get_graph(client, settings=settings)
        facts, unparsed = load_dispositions(nodes_file, process_master_file)
        summary = apply_dispositions(graph, facts, get_logger("cli"))
    finally:
        close_client(client)

    _print_disposition_summary(summary, len(unparsed))


def _ask(*, question: str, show_cypher: bool) -> None:
    settings = load_settings()
    client = build_client(settings)
    anthropic_client = build_anthropic_client(settings)
    try:
        graph = get_graph(client, settings=settings)
        schema_context = build_schema_context(graph)
        agent = AsIsQueryAgent(
            anthropic_client,
            graph,
            model=settings.anthropic_model,
            schema_context=schema_context,
            logger=get_logger("cli"),
        )
        result = agent.ask(question)
    finally:
        close_client(client)

    if show_cypher and result.cypher:
        print(f"[cypher] {result.cypher}")
    print(result.answer)
    if result.blocked:
        sys.exit(1)


def _route(*, question: str) -> None:
    settings = load_settings()
    client = build_client(settings)
    anthropic_client = build_anthropic_client(settings)
    try:
        graph = get_graph(client, settings=settings)
        schema_context = build_schema_context(graph)
        app = build_orchestration_graph(
            anthropic_client,
            graph,
            model=settings.anthropic_model,
            schema_context=schema_context,
            logger=get_logger("cli"),
        )
        result = route_question(app, question)
    finally:
        close_client(client)

    print(f"[route: {result['route']}]")
    print(result["final_answer"])
    if result.get("blocked"):
        sys.exit(1)


def _print_mapping_report(report: MappingReport) -> None:
    for c in report.coverage:
        print(f"[{c.module}] {c.mapped_transactions}/{c.total_transactions} mapped")
        for source, target in c.mapped_pairs:
            print(f"    {source} -> {target}")
    if report.narrative:
        print("\n--- narrative ---")
        print(report.narrative)


def _map(*, module: str | None, narrate: bool) -> None:
    settings = load_settings()
    client = build_client(settings)
    anthropic_client = build_anthropic_client(settings) if narrate else None
    try:
        graph = get_graph(client, settings=settings)
        report = build_mapping_report(
            graph,
            module=module,
            narrate=narrate,
            client=anthropic_client,
            model=settings.anthropic_model,
        )
    finally:
        close_client(client)

    _print_mapping_report(report)


def _print_compliance_report(report: ComplianceReport) -> None:
    for f in report.findings:
        tag = "[FLAGSHIP]" if f.is_flagship else "[finding]"
        print(
            f"{tag} {f.source_id} -[{f.relation} ({f.edge_confidence})]-> {f.target_id} "
            f"(target_gxp={f.target_gxp!r})"
        )
    for s4 in report.s4_findings:
        print(
            f"[S4-CATALOG] {s4.node_id} ({s4.label!r}): s4_status={s4.s4_status!r}, "
            f"s4_severity={s4.s4_severity!r} (s4_confidence={s4.s4_confidence!r})"
        )
    if report.narrative:
        print("\n--- narrative ---")
        print(report.narrative)


def _compliance_scan(*, narrate: bool) -> None:
    settings = load_settings()
    client = build_client(settings)
    anthropic_client = build_anthropic_client(settings) if narrate else None
    try:
        graph = get_graph(client, settings=settings)
        report = build_compliance_report(
            graph, narrate=narrate, client=anthropic_client, model=settings.anthropic_model
        )
    finally:
        close_client(client)

    _print_compliance_report(report)


def _db_check(*, deep: bool) -> None:
    settings = load_settings()
    client = build_client(settings)
    try:
        report = run_health_checks(client, settings=settings, deep=deep)
    finally:
        close_client(client)

    _print_health_report(report)
    if not report.healthy:
        sys.exit(1)


def main(argv: list[str] | None = None) -> None:
    """`kgme db check [--deep]`, `kgme enrich s4-catalog [--file ...]`,
    `kgme enrich disposition [...]`, `kgme ask "<question>" [--show-cypher]`,
    `kgme route "<question>"`, `kgme map [--module ...] [--narrate]`, and
    `kgme compliance-scan [--narrate]`."""
    parser = argparse.ArgumentParser(prog="kgme")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ask_parser = subparsers.add_parser("ask", help="ask a natural-language question (As-Is agent)")
    ask_parser.add_argument("question", help="the natural-language question")
    ask_parser.add_argument(
        "--show-cypher", action="store_true", help="print the generated Cypher query"
    )

    db_parser = subparsers.add_parser("db", help="database operations")
    db_subparsers = db_parser.add_subparsers(dest="db_command", required=True)
    check_parser = db_subparsers.add_parser("check", help="readiness check")
    check_parser.add_argument("--deep", action="store_true")

    enrich_parser = subparsers.add_parser("enrich", help="enrichment operations")
    enrich_subparsers = enrich_parser.add_subparsers(dest="enrich_command", required=True)
    s4_parser = enrich_subparsers.add_parser(
        "s4-catalog", help="enrich matching nodes with SAP Simplification Catalog properties"
    )
    s4_parser.add_argument(
        "--file",
        type=Path,
        default=DEFAULT_S4_CATALOG_PATH,
        help="path to the extracted Simplification Catalog JSON",
    )
    disposition_parser = enrich_subparsers.add_parser(
        "disposition",
        help=(
            "derive MIGRATES_TO edges / disposition_* properties from "
            "NovaPharm Biologics's own data"
        ),
    )
    disposition_parser.add_argument(
        "--nodes-file", type=Path, default=DEFAULT_NODES_PATH, help="path to kg_nodes.csv"
    )
    disposition_parser.add_argument(
        "--process-master-file",
        type=Path,
        default=DEFAULT_PROCESS_MASTER_PATH,
        help="path to kg_process_master.csv",
    )

    map_parser = subparsers.add_parser("map", help="report MIGRATES_TO coverage by module")
    map_parser.add_argument("--module", choices=["MM", "AM", "cross", "governance"], default=None)
    map_parser.add_argument(
        "--narrate", action="store_true", help="add an LLM-composed prose summary"
    )

    route_parser = subparsers.add_parser(
        "route",
        help="classify a question and dispatch to the right agent (As-Is/Mapping/Compliance)",
    )
    route_parser.add_argument("question", help="the natural-language question")

    compliance_parser = subparsers.add_parser(
        "compliance-scan", help="surface GxP-compliance risk findings (gap/inferred/GxP-kritisch)"
    )
    compliance_parser.add_argument(
        "--narrate", action="store_true", help="add an LLM-composed prose summary"
    )

    args = parser.parse_args(argv)

    if args.command == "ask":
        _ask(question=args.question, show_cypher=args.show_cypher)
    elif args.command == "db" and args.db_command == "check":
        _db_check(deep=args.deep)
    elif args.command == "enrich" and args.enrich_command == "s4-catalog":
        _enrich_s4_catalog(file=args.file)
    elif args.command == "enrich" and args.enrich_command == "disposition":
        _enrich_disposition(
            nodes_file=args.nodes_file, process_master_file=args.process_master_file
        )
    elif args.command == "route":
        _route(question=args.question)
    elif args.command == "map":
        _map(module=args.module, narrate=args.narrate)
    elif args.command == "compliance-scan":
        _compliance_scan(narrate=args.narrate)
