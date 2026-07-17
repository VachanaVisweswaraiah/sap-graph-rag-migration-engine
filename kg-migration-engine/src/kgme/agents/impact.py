"""NOTE: This file has been sanitized for public/private portfolio use.
Business logic, domain-specific rules, and proprietary details have been masked.
The coding patterns, architecture, and technical implementation remain authentic.
[MASKED] tags indicate where original business logic has been replaced.

Cross-Module Impact Agent: surfaces the LINKED_VIA_INVESTMENT -> ... ->
RECONCILES_TO reconciliation chain(s) connecting the otherwise-separate MM and AM
document worlds. // [MASKED] Internal business context removed (this agent
is understood internally to expose "the most analytically interesting path
in the graph").

Deterministic core, no LLM involved in detection — the query is fixed and
generalized (matches the relation *types*, never a hardcoded node_id), mirroring
agents/compliance.py's and agents/mapping.py's trust model. An optional narration
layer turns already-computed chains into prose; it never invents a chain that
wasn't returned by the query.

Scope is deliberately narrow: this agent reports inbound-edge counts only for the
nodes inside a found chain, not graph-wide centrality. General centrality ranking
stays out of scope until an ATC scan closes the custom-object dark field (see
CLAUDE.md's "deferred by design" note and dashboard/templates/gaps.html's
"low signal until ATC scan" caption) — this agent must not overclaim beyond what
the confidence-graded chain itself already tells you.
"""

from __future__ import annotations

from dataclasses import dataclass

import anthropic
from falkordb import Graph

_CHAIN_QUERY = """
MATCH (start)-[e1:LINKED_VIA_INVESTMENT]->(mid1)
MATCH (mid1)-[e2]->(mid2)
MATCH (mid2)-[e3:RECONCILES_TO]->(start)
RETURN start.node_id AS start_id, start.confidence AS start_confidence,
       start.module AS start_module,
       type(e1) AS rel1, e1.confidence AS rel1_confidence, e1.source_doc AS rel1_source_doc,
       mid1.node_id AS mid1_id, mid1.confidence AS mid1_confidence, mid1.module AS mid1_module,
       type(e2) AS rel2, e2.confidence AS rel2_confidence, e2.source_doc AS rel2_source_doc,
       mid2.node_id AS mid2_id, mid2.confidence AS mid2_confidence, mid2.module AS mid2_module,
       type(e3) AS rel3, e3.confidence AS rel3_confidence, e3.source_doc AS rel3_source_doc
"""

_INBOUND_COUNT_QUERY = """
MATCH (n {node_id: $node_id})<-[r]-(other)
RETURN count(r) AS inbound_total,
       count(CASE WHEN r.confidence = 'documented' THEN 1 END) AS inbound_documented,
       count(CASE WHEN other.confidence = 'gap' OR r.confidence = 'inferred' THEN 1 END)
           AS weak_neighbors
"""

_CONFIDENCE_RANK: dict[str, int] = {"gap": 0, "inferred": 1, "peripheral": 2, "documented": 3}


@dataclass(frozen=True)
class ChainHop:
    node_id: str
    confidence: str | None
    module: str | None
    inbound_total: int
    inbound_documented: int
    weak_neighbors: int


@dataclass(frozen=True)
class ReconciliationChain:
    start: ChainHop
    investment_relation: str
    investment_confidence: str | None
    investment_source_doc: str | None
    mid1: ChainHop
    intermediate_relation: str
    intermediate_confidence: str | None
    intermediate_source_doc: str | None
    mid2: ChainHop
    reconciliation_relation: str
    reconciliation_confidence: str | None
    reconciliation_source_doc: str | None

    @property
    def weakest_link_confidence(self) -> str | None:
        """The lowest-trust confidence among the chain's three edges — the
        signal for "does a migration change risk breaking the reconciliation"."""
        edge_confidences = [
            self.investment_confidence,
            self.intermediate_confidence,
            self.reconciliation_confidence,
        ]
        known = [c for c in edge_confidences if c in _CONFIDENCE_RANK]
        if not known:
            return None
        return min(known, key=lambda c: _CONFIDENCE_RANK[c])


@dataclass(frozen=True)
class ImpactReport:
    chains: list[ReconciliationChain]
    narrative: str | None


def _hop(graph: Graph, *, node_id: str, confidence: str | None, module: str | None) -> ChainHop:
    result = graph.ro_query(_INBOUND_COUNT_QUERY, {"node_id": node_id})
    inbound_total, inbound_documented, weak_neighbors = result.result_set[0]
    return ChainHop(
        node_id=node_id,
        confidence=confidence,
        module=module,
        inbound_total=inbound_total,
        inbound_documented=inbound_documented,
        weak_neighbors=weak_neighbors,
    )


def compute_reconciliation_chains(graph: Graph) -> list[ReconciliationChain]:
    """Deterministic. Finds every LINKED_VIA_INVESTMENT -> * -> RECONCILES_TO
    triangle in the graph by relation type (never a hardcoded node_id), then
    reports real inbound-edge counts for each of the three nodes involved —
    scoped only to those nodes, not a graph-wide centrality scan."""
    result = graph.ro_query(_CHAIN_QUERY)
    chains: list[ReconciliationChain] = []
    for (
        start_id,
        start_confidence,
        start_module,
        rel1,
        rel1_confidence,
        rel1_source_doc,
        mid1_id,
        mid1_confidence,
        mid1_module,
        rel2,
        rel2_confidence,
        rel2_source_doc,
        mid2_id,
        mid2_confidence,
        mid2_module,
        rel3,
        rel3_confidence,
        rel3_source_doc,
    ) in result.result_set:
        chains.append(
            ReconciliationChain(
                start=_hop(
                    graph, node_id=start_id, confidence=start_confidence, module=start_module
                ),
                investment_relation=rel1,
                investment_confidence=rel1_confidence,
                investment_source_doc=rel1_source_doc,
                mid1=_hop(graph, node_id=mid1_id, confidence=mid1_confidence, module=mid1_module),
                intermediate_relation=rel2,
                intermediate_confidence=rel2_confidence,
                intermediate_source_doc=rel2_source_doc,
                mid2=_hop(graph, node_id=mid2_id, confidence=mid2_confidence, module=mid2_module),
                reconciliation_relation=rel3,
                reconciliation_confidence=rel3_confidence,
                reconciliation_source_doc=rel3_source_doc,
            )
        )
    return chains


def narrate_impact_report(
    client: anthropic.Anthropic, *, model: str, chains: list[ReconciliationChain]
) -> str:
    """One plain LLM call turning already-computed chains into prose. Explicitly
    instructed to call out the weakest-confidence hop and never invent a chain,
    node, or centrality claim beyond what's given."""
    if not chains:
        lines = ["No LINKED_VIA_INVESTMENT -> RECONCILES_TO reconciliation chain was found."]
    else:
        lines = [
            f"- {c.start.node_id} ({c.start.confidence}, module={c.start.module}) "
            f"-[{c.investment_relation} ({c.investment_confidence})]-> "
            f"{c.mid1.node_id} ({c.mid1.confidence}, module={c.mid1.module}) "
            f"-[{c.intermediate_relation} ({c.intermediate_confidence})]-> "
            f"{c.mid2.node_id} ({c.mid2.confidence}, module={c.mid2.module}) "
            f"-[{c.reconciliation_relation} ({c.reconciliation_confidence})]-> "
            f"{c.start.node_id}. Weakest link confidence: {c.weakest_link_confidence}. "
            f"Inbound edges — {c.start.node_id}: {c.start.inbound_documented}/"
            f"{c.start.inbound_total} documented, {c.start.weak_neighbors} weak neighbors; "
            f"{c.mid1.node_id}: {c.mid1.inbound_documented}/{c.mid1.inbound_total} documented, "
            f"{c.mid1.weak_neighbors} weak neighbors; "
            f"{c.mid2.node_id}: {c.mid2.inbound_documented}/{c.mid2.inbound_total} documented, "
            f"{c.mid2.weak_neighbors} weak neighbors."
            for c in chains
        ]
    response = client.messages.create(
        model=model,
        max_tokens=1024,
        system=(
            "You summarize cross-module dependency chains for a SAP ECC-to-S/4HANA "
            "migration — specifically the LINKED_VIA_INVESTMENT -> RECONCILES_TO chain "
            "that connects the MM (materials management) and AM (asset accounting) "
            "document worlds. Always name the weakest-confidence hop and explain why a "
            "migration change to that hop risks breaking the reconciliation. Report only "
            "the chains and inbound-edge counts given to you — never invent a chain, and "
            "never claim a general centrality ranking; the counts given are scoped only "
            "to the nodes in this chain, not the whole graph."
        ),
        messages=[{"role": "user", "content": "Chains:\n" + "\n".join(lines)}],
    )
    for block in response.content:
        if block.type == "text":
            return str(block.text)
    return "No narrative text was returned."


def build_impact_report(
    graph: Graph,
    *,
    narrate: bool = False,
    client: anthropic.Anthropic | None = None,
    model: str = "",
) -> ImpactReport:
    chains = compute_reconciliation_chains(graph)
    narrative = None
    if narrate:
        if client is None:
            raise ValueError("narrate=True requires a client")
        narrative = narrate_impact_report(client, model=model, chains=chains)
    return ImpactReport(chains=chains, narrative=narrative)
