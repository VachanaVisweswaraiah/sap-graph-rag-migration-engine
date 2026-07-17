"""Gaps Agent: turns the fixed, no-LLM listing from db/gaps.py (gap-confidence
nodes + inferred-confidence edges) into a plain inventory narrative — the "what
do we still need before we can decide?" question category, distinct from
agents/compliance.py's GxP-risk-narrative framing of overlapping data.

Deterministic core (delegates entirely to db.gaps.list_gap_nodes/
list_inferred_edges, unchanged); an optional narration layer turns the
already-fetched lists into prose. Per the hand-over README's own framing, gap
nodes and inferred edges "are retrieval/interview targets, not facts" — the
narration prompt preserves that framing, never presenting them as settled
answers.
"""

from __future__ import annotations

from dataclasses import dataclass

import anthropic
from falkordb import Graph

from kgme.db.gaps import GapNode, InferredEdge, list_gap_nodes, list_inferred_edges


@dataclass(frozen=True)
class GapsReport:
    gap_nodes: list[GapNode]
    inferred_edges: list[InferredEdge]
    narrative: str | None


def narrate_gaps_report(
    client: anthropic.Anthropic,
    *,
    model: str,
    gap_nodes: list[GapNode],
    inferred_edges: list[InferredEdge],
) -> str:
    """One plain LLM call turning the already-fetched gap nodes/inferred edges
    into prose. Explicitly instructed to frame them as retrieval targets, not
    settled facts, and to report only what's given — never invent an item or
    silently drop an empty list instead of stating it plainly."""
    gap_lines = [
        f"- {n.node_id} ({n.node_type}, module={n.module}, referenced by "
        f"source_doc={n.source_doc!r})"
        for n in gap_nodes
    ]
    edge_lines = [
        f"- {e.source_id} -[{e.relation}]-> {e.target_id} (source_doc={e.source_doc!r})"
        for e in inferred_edges
    ]
    content = (
        f"Gap nodes ({len(gap_nodes)}):\n"
        + ("\n".join(gap_lines) if gap_lines else "(none)")
        + f"\n\nInferred edges ({len(inferred_edges)}):\n"
        + ("\n".join(edge_lines) if edge_lines else "(none)")
    )
    response = client.messages.create(
        model=model,
        max_tokens=1024,
        system=(
            "You summarize the documentation gaps in a SAP ECC-to-S/4HANA GxP migration "
            "knowledge graph: nodes that are referenced or conceptually required but whose "
            "content was never received (confidence='gap'), and edges that are the analyst's "
            "own inferred hypothesis rather than a sourced fact (confidence='inferred'). "
            "Present these as retrieval/interview targets — things the team still needs to "
            "chase down — never as settled facts. Report ONLY the items given to you, state "
            "the counts explicitly, and if a list is empty say so plainly rather than omitting "
            "it."
        ),
        messages=[{"role": "user", "content": content}],
    )
    for block in response.content:
        if block.type == "text":
            return str(block.text)
    return "No narrative text was returned."


def build_gaps_report(
    graph: Graph,
    *,
    narrate: bool = False,
    client: anthropic.Anthropic | None = None,
    model: str = "",
) -> GapsReport:
    gap_nodes = list_gap_nodes(graph)
    inferred_edges = list_inferred_edges(graph)
    narrative = None
    if narrate:
        if client is None:
            raise ValueError("narrate=True requires a client")
        narrative = narrate_gaps_report(
            client, model=model, gap_nodes=gap_nodes, inferred_edges=inferred_edges
        )
    return GapsReport(gap_nodes=gap_nodes, inferred_edges=inferred_edges, narrative=narrative)
