"""NOTE: This file has been sanitized for public/private portfolio use.
Business logic, domain-specific rules, and proprietary details have been masked.
The coding patterns, architecture, and technical implementation remain authentic.
[MASKED] tags indicate where original business logic has been replaced.

Migration-Mapping Agent: reports MIGRATES_TO coverage scoped by module (the
graph's real segmentation field — value_stream only exists in the never-graph-loaded
kg_process_master.csv, same substitution already made in schema_context.py).

Deterministic core, no LLM involved in computing coverage — the coverage numbers ARE
the fact. An optional narration layer turns already-computed numbers into prose;
it never estimates or invents a module's coverage itself (see narrate_mapping_report).

Honest reporting is the whole point: a module with zero MIGRATES_TO edges is reported
as zero, not silently omitted — e.g. AM is the one fully-documented module yet has
zero migration edges today, because its disposition text was a conceptual note, not
a literal code-level redirect (see docs/AUDIT.md).
"""

from __future__ import annotations

from dataclasses import dataclass

import anthropic
from falkordb import Graph

MODULES: tuple[str, ...] = ("MM", "AM", "cross", "governance")

_COVERAGE_QUERY = """
MATCH (n:Entity:Transaction {module: $module})
OPTIONAL MATCH (n)-[:MIGRATES_TO]->(t)
RETURN count(DISTINCT n) AS total,
       count(DISTINCT CASE WHEN t IS NOT NULL THEN n END) AS mapped,
       collect(DISTINCT [n.node_id, t.node_id]) AS pairs
"""


@dataclass(frozen=True)
class ModuleCoverage:
    module: str
    total_transactions: int
    mapped_transactions: int
    mapped_pairs: list[tuple[str, str]]


@dataclass(frozen=True)
class MappingReport:
    coverage: list[ModuleCoverage]
    narrative: str | None


def compute_mapping_coverage(graph: Graph, *, module: str | None = None) -> list[ModuleCoverage]:
    """Deterministic. Reports every requested module honestly, including modules
    with zero MIGRATES_TO edges — never omits a module just because it has no
    coverage yet."""
    modules = (module,) if module is not None else MODULES
    coverage: list[ModuleCoverage] = []
    for mod in modules:
        result = graph.ro_query(_COVERAGE_QUERY, {"module": mod})
        total, mapped, pairs = result.result_set[0]
        mapped_pairs = [(source, target) for source, target in pairs if target is not None]
        coverage.append(
            ModuleCoverage(
                module=mod,
                total_transactions=total,
                mapped_transactions=mapped,
                mapped_pairs=mapped_pairs,
            )
        )
    return coverage


def narrate_mapping_report(
    client: anthropic.Anthropic,
    *,
    model: str,
    coverage: list[ModuleCoverage],
    question: str | None = None,
) -> str:
    """One plain LLM call turning already-computed coverage numbers into prose.
    Explicitly instructed to report only the given numbers — never estimate or
    fill in a module that wasn't in the input data.

    When `question` is given, the model answers that specific question (e.g. a
    quick percentage) instead of always producing the full coverage report —
    the standalone CLI report path (no question) keeps the full report."""
    lines = [
        f"- {c.module}: {c.mapped_transactions}/{c.total_transactions} transactions have a "
        f"known MIGRATES_TO target. Mapped pairs: {c.mapped_pairs}"
        for c in coverage
    ]
    system = (
        "You summarize S/4HANA migration-mapping coverage for a GxP-regulated SAP "
        "migration. Report ONLY the numbers given to you — never estimate, round "
        "favorably, or imply coverage exists for a module that shows 0 mapped. "
        "A module with 0 mapped transactions must be stated as a real gap, not "
        "glossed over, even if that module is otherwise well documented. "
        "Whenever you discuss a mapped pair, explicitly state that every MIGRATES_TO "
        "edge is the analyst's own inferred migration call — derived from reading the "
        "source documents, not a fact directly stated in them — and should be "
        "confirmed with NovaPharm Biologics SMEs before being relied on for migration "
        "execution. Use the word 'opinion' or 'inferred' when making this caveat, never "
        "imply these mappings are settled, sourced fact."
    )
    if question is not None:
        system += (
            " The user asked a specific question — answer THAT question directly and "
            "concisely using only the coverage data above. If they asked for a single "
            "figure (e.g. a percentage or count), lead with that figure in the first "
            "sentence; don't restate the full per-module report unless they actually "
            "asked for an overview of every module."
        )
    user_content = "Coverage data:\n" + "\n".join(lines)
    if question is not None:
        user_content = f"Question: {question}\n\n{user_content}"
    response = client.messages.create(
        model=model,
        max_tokens=1024,
        system=system,
        messages=[{"role": "user", "content": user_content}],
    )
    for block in response.content:
        if block.type == "text":
            return str(block.text)
    return "No narrative text was returned."


def build_mapping_report(
    graph: Graph,
    *,
    module: str | None = None,
    narrate: bool = False,
    client: anthropic.Anthropic | None = None,
    model: str = "",
    question: str | None = None,
) -> MappingReport:
    coverage = compute_mapping_coverage(graph, module=module)
    narrative = None
    if narrate:
        if client is None:
            raise ValueError("narrate=True requires a client")
        narrative = narrate_mapping_report(
            client, model=model, coverage=coverage, question=question
        )
    return MappingReport(coverage=coverage, narrative=narrative)
