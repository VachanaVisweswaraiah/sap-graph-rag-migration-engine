"""NOTE: This file has been sanitized for public/private portfolio use.
Business logic, domain-specific rules, and proprietary details have been masked.
The coding patterns, architecture, and technical implementation remain authentic.
[MASKED] tags indicate where original business logic has been replaced.

GxP-Compliance Agent: surfaces paths where a node is a documentation `gap`,
an edge is `inferred`, or a target is `GxP-kritisch` — the three conditions that
signal migration-risk to a GxP-regulated process (see CLAUDE.md's compliance rule).

Deterministic core, no LLM involved in detection — the detection query is fixed
and hand-verified (docs/IMPLEMENTATION_PLAN.md SS3.5), never LLM-generated. Letting
an LLM invent this query would reintroduce exactly the risk the confidence model
exists to guard against. An optional narration layer turns already-ranked findings
into prose; it never re-derives or reranks them.

The `SUSPECTED_SOURCE` edge QM:BATCH_RELEASE -> SYS:LAB_SYSTEM (RA_PROC01 risk #7) is the
flagship finding: an automated batch-release decision with an unspecified upstream
interface. It is pinned first in every report regardless of its severity tier,
per CLAUDE.md's explicit "surface it first" requirement.

A second, independent risk category comes from `enrichment/s4_simplification.py`'s
`s4_severity` property — a node the official SAP Simplification Catalog flags as
breaking during migration. This is deliberately a SEPARATE query, not an extra `OR`
clause bolted onto `_COMPLIANCE_QUERY` above: that query requires a relationship
(`MATCH path = (src)-[e]->(t)`), but most s4-flagged nodes (16 of 22, verified live)
have zero relationships at all — e.g. TX:AS21 is a documented AM node with no edges
in this graph. A path-based query would silently miss almost all of them.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import anthropic
from falkordb import Graph

_COMPLIANCE_QUERY = """
MATCH path = (src)-[e]->(t)
WHERE t.gxp_classification = 'GxP-kritisch'
   OR any(n IN nodes(path) WHERE n.confidence IN ['gap'])
   OR any(r IN relationships(path) WHERE r.confidence = 'inferred')
RETURN src.node_id AS source_id, type(e) AS relation, t.node_id AS target_id,
       e.confidence AS edge_confidence, t.gxp_classification AS target_gxp,
       e.source_doc AS source_doc
"""

_FLAGSHIP_SOURCE = "QM:BATCH_RELEASE"
_FLAGSHIP_TARGET = "SYS:LAB_SYSTEM"
_FLAGSHIP_RELATION = "SUSPECTED_SOURCE"

# Node-level, no relationship required -- see the module docstring for why this
# can't just be another OR clause on _COMPLIANCE_QUERY. Reports every flagged
# severity the catalog produced; deciding which severities are "important enough"
# belongs to the reader, not this scan (same "report only what's given, never
# invent a threshold" discipline as mapping.py's coverage reporting).
_S4_CATALOG_QUERY = """
MATCH (n) WHERE n.s4_severity IS NOT NULL
RETURN n.node_id AS node_id, n.label AS label, n.module AS module,
       n.s4_status AS s4_status, n.s4_severity AS s4_severity,
       n.s4_target AS s4_target, n.s4_confidence AS s4_confidence,
       n.s4_source_doc AS s4_source_doc
"""


@dataclass(frozen=True)
class ComplianceFinding:
    source_id: str
    relation: str
    target_id: str
    edge_confidence: str | None
    target_gxp: str | None
    source_doc: str | None
    is_flagship: bool


@dataclass(frozen=True)
class S4CatalogFinding:
    node_id: str
    label: str | None
    module: str | None
    s4_status: str | None
    s4_severity: str
    s4_target: str | None
    s4_confidence: str | None
    s4_source_doc: str | None


@dataclass(frozen=True)
class ComplianceReport:
    findings: list[ComplianceFinding]
    s4_findings: list[S4CatalogFinding]
    narrative: str | None


def _is_flagship(finding: ComplianceFinding) -> bool:
    return (
        finding.source_id == _FLAGSHIP_SOURCE
        and finding.target_id == _FLAGSHIP_TARGET
        and finding.relation == _FLAGSHIP_RELATION
    )


def _severity_tier(finding: ComplianceFinding) -> int:
    """0 = flagship (pinned first regardless), 1 = inferred edge, 2 = gap-touching
    documented path, 3 = documented path to a GxP-kritisch target (context, not a
    gap). Matches the real tiers observed in the live compliance scan."""
    if finding.is_flagship:
        return 0
    if finding.edge_confidence == "inferred":
        return 1
    if finding.target_gxp != "GxP-kritisch":
        return 2
    return 3


def run_compliance_scan(graph: Graph) -> list[ComplianceFinding]:
    """Executes the fixed detection query via read-only Cypher, tags the flagship
    finding, and sorts by severity tier with the flagship pinned first."""
    result = graph.ro_query(_COMPLIANCE_QUERY)
    findings = []
    for (
        source_id,
        relation,
        target_id,
        edge_confidence,
        target_gxp,
        source_doc,
    ) in result.result_set:
        finding = ComplianceFinding(
            source_id=source_id,
            relation=relation,
            target_id=target_id,
            edge_confidence=edge_confidence,
            target_gxp=target_gxp,
            source_doc=source_doc,
            is_flagship=False,
        )
        if _is_flagship(finding):
            finding = replace(finding, is_flagship=True)
        findings.append(finding)
    return sorted(findings, key=_severity_tier)


def run_s4_catalog_scan(graph: Graph) -> list[S4CatalogFinding]:
    """Deterministic, no LLM. Independent of run_compliance_scan() above -- see
    the module docstring for why a node-level query, not a path-based one."""
    result = graph.ro_query(_S4_CATALOG_QUERY)
    return [
        S4CatalogFinding(
            node_id=node_id,
            label=label,
            module=module,
            s4_status=s4_status,
            s4_severity=s4_severity,
            s4_target=s4_target,
            s4_confidence=s4_confidence,
            s4_source_doc=s4_source_doc,
        )
        for (
            node_id,
            label,
            module,
            s4_status,
            s4_severity,
            s4_target,
            s4_confidence,
            s4_source_doc,
        ) in result.result_set
    ]


def narrate_compliance_report(
    client: anthropic.Anthropic,
    *,
    model: str,
    findings: list[ComplianceFinding],
    s4_findings: list[S4CatalogFinding] | None = None,
    question: str | None = None,
) -> str:
    """One plain LLM call turning already-ranked findings into prose. The
    flagship finding's regulatory context (RA_PROC01 risk #7, automated
    batch-release decision, unspecified interface) is anchored in the prompt so
    the model contextualizes it rather than re-deriving it from scratch.

    When `question` is given, the model is told to answer that specific
    question — not to always restate the full report. Without a question (the
    standalone CLI report path), it produces the full report as before."""
    s4_findings = s4_findings or []
    lines = [
        f"- [{'FLAGSHIP' if f.is_flagship else 'finding'}] {f.source_id} "
        f"-[{f.relation} ({f.edge_confidence})]-> {f.target_id} "
        f"(target gxp_classification={f.target_gxp!r}, source_doc={f.source_doc!r})"
        for f in findings
    ]
    s4_lines = [
        f"- [S4-CATALOG] {f.node_id} ({f.label!r}, module={f.module}): "
        f"s4_status={f.s4_status!r}, s4_severity={f.s4_severity!r}, "
        f"s4_target={f.s4_target!r} (s4_confidence={f.s4_confidence!r}, "
        f"s4_source_doc={f.s4_source_doc!r})"
        for f in s4_findings
    ]
    system = (
        "You summarize GxP-compliance risk findings for a SAP ECC-to-S/4HANA "
        "migration. The finding marked FLAGSHIP is QM:BATCH_RELEASE's "
        "SUSPECTED_SOURCE edge to SYS:LAB_SYSTEM — an automated batch-release decision "
        "(RA_PROC01 risk #7) with an unspecified upstream interface. Always mention "
        "it first and explain why it matters. Report only the findings given to "
        "you — never invent or omit one. Findings marked [S4-CATALOG] are a "
        "SEPARATE risk category — nodes the official SAP Simplification Catalog "
        "flags as breaking during migration, independent of this graph's own GxP "
        "documentation. Report them as their own section, distinct from the "
        "GxP-risk findings above, and always cite the s4_confidence value alongside "
        "any s4_status/s4_severity/s4_target you state — never present catalog data "
        "as settled fact without it."
    )
    if question is not None:
        system += (
            " The user asked a specific question — answer THAT question directly "
            "and concisely, using only the findings above as your evidence. If the "
            "question is narrow (e.g. yes/no, or about one specific finding), give a "
            "short, direct answer first and don't restate the full findings list "
            "unless the question actually asks for an overview or a list of risks."
        )
    user_content = "GxP findings:\n" + "\n".join(lines) if lines else "GxP findings: (none)"
    user_content += "\n\nS/4 catalog findings:\n" + ("\n".join(s4_lines) if s4_lines else "(none)")
    if question is not None:
        user_content = f"Question: {question}\n\n{user_content}"
    # 1024 was enough when this only had to cover the GxP path-based findings; now
    # that a broad question also has to cover every S4-catalog finding without
    # omission, the combined report can genuinely need more room. Found live: a
    # "what are all the risks" question truncated mid-word before ever reaching
    # the S4-catalog section.
    response = client.messages.create(
        model=model,
        max_tokens=2048,
        system=system,
        messages=[{"role": "user", "content": user_content}],
    )
    for block in response.content:
        if block.type == "text":
            return str(block.text)
    return "No narrative text was returned."


def build_compliance_report(
    graph: Graph,
    *,
    narrate: bool = False,
    client: anthropic.Anthropic | None = None,
    model: str = "",
    question: str | None = None,
) -> ComplianceReport:
    findings = run_compliance_scan(graph)
    s4_findings = run_s4_catalog_scan(graph)
    narrative = None
    if narrate:
        if client is None:
            raise ValueError("narrate=True requires a client")
        narrative = narrate_compliance_report(
            client, model=model, findings=findings, s4_findings=s4_findings, question=question
        )
    return ComplianceReport(findings=findings, s4_findings=s4_findings, narrative=narrative)
