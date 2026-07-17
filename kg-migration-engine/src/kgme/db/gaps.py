"""Fixed, no-LLM listing of the graph's gap nodes and inferred edges — the two
confidence-model markers CLAUDE.md defines as GxP risk signals (node
confidence='gap', edge confidence='inferred'; no edge is ever 'gap', no node is
ever 'inferred'). Same trust model as agents/mapping.py and agents/compliance.py:
fixed, hand-verified Cypher, never LLM-generated.
"""

from __future__ import annotations

from dataclasses import dataclass

from falkordb import Graph

_GAP_NODES_QUERY = """
MATCH (n)
WHERE n.confidence = 'gap'
RETURN n.node_id AS node_id, n.node_type AS node_type, n.module AS module,
       n.source_doc AS source_doc
"""

_INFERRED_EDGES_QUERY = """
MATCH (s)-[r]->(t)
WHERE r.confidence = 'inferred'
RETURN s.node_id AS source_id, type(r) AS relation, t.node_id AS target_id,
       r.source_doc AS source_doc
"""


@dataclass(frozen=True)
class GapNode:
    node_id: str
    node_type: str | None
    module: str | None
    source_doc: str | None


@dataclass(frozen=True)
class InferredEdge:
    source_id: str
    relation: str
    target_id: str
    source_doc: str | None


def list_gap_nodes(graph: Graph) -> list[GapNode]:
    result = graph.ro_query(_GAP_NODES_QUERY)
    return [
        GapNode(node_id=node_id, node_type=node_type, module=module, source_doc=source_doc)
        for node_id, node_type, module, source_doc in result.result_set
    ]


def list_inferred_edges(graph: Graph) -> list[InferredEdge]:
    result = graph.ro_query(_INFERRED_EDGES_QUERY)
    return [
        InferredEdge(
            source_id=source_id, relation=relation, target_id=target_id, source_doc=source_doc
        )
        for source_id, relation, target_id, source_doc in result.result_set
    ]
