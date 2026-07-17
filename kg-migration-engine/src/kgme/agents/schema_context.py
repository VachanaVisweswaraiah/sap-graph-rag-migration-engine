"""Builds the schema-description text injected into every agent's system prompt, so
the model generates Cypher against *this* graph's actual shape rather than a guess.

Queries the LIVE graph (not the static CSVs) — this keeps the context automatically
accurate as the graph evolves without any code change (e.g. it picks up Phase 2's
MIGRATES_TO relation type for free). Run once per agent construction, not per
question — these are cheap DISTINCT scans, but there's no reason to repeat them.
"""

from __future__ import annotations

from falkordb import Graph

# Every property named here is already described explicitly in the NODE PROPERTIES
# line below (or is edge-only, like edge_id). Anything else that shows up on the
# live graph is from an enrichment script (disposition.py, s4_simplification.py, or
# any future one) and must be surfaced dynamically -- see _extra_property_keys.
_BASELINE_PROPERTY_KEYS = frozenset(
    {
        "node_id",
        "node_type",
        "label",
        "module",
        "gxp_classification",
        "confidence",
        "source_doc",
        "source_ref",
        "notes",
        "edge_id",
    }
)


def _distinct(graph: Graph, cypher: str) -> list[str]:
    result = graph.ro_query(cypher)
    return sorted({row[0] for row in result.result_set if row[0] not in (None, "")})


def _extra_property_keys(graph: Graph) -> list[str]:
    """Enrichment scripts add namespaced properties (disposition_*, s4_*, and any
    future family) that aren't in the static NODE PROPERTIES list -- unlike
    node_types/relation_types/confidence values below, that list was hand-written,
    not queried live, so it silently went stale the moment an enrichment script ran
    for the first time. Found by testing: asking the As-Is agent about a node that
    genuinely had s4_status/s4_severity set produced "no such data" -- a factually
    wrong refusal, not a crash, exactly the kind of confidently-wrong answer this
    project's design exists to prevent. Querying db.propertyKeys() live closes that
    gap the same way the rest of this file already avoids hardcoding graph content."""
    result = graph.ro_query("CALL db.propertyKeys() YIELD propertyKey RETURN propertyKey")
    return sorted(row[0] for row in result.result_set if row[0] not in _BASELINE_PROPERTY_KEYS)


def _example_ids_by_node_type(graph: Graph) -> list[tuple[str, str]]:
    """One real example node_id per node_type, so the model sees the actual
    node_id prefix convention (e.g. PROC:MM01, not MM01) instead of guessing it
    from the label name alone — a real gap found during manual verification: the
    model initially guessed a bare 'MM01' node_id and a nonexistent `t.name`
    property, and correctly (safely) reported 'no results' rather than fabricate
    an answer — but the answer would have been more useful with this context."""
    result = graph.ro_query(
        "MATCH (n:Entity) WITH n.node_type AS node_type, n.node_id AS node_id "
        "ORDER BY node_id RETURN node_type, collect(node_id)[0] AS example_id"
    )
    return sorted((row[0], row[1]) for row in result.result_set if row[0] not in (None, ""))


def build_schema_context(graph: Graph) -> str:
    node_types = _distinct(graph, "MATCH (n:Entity) RETURN DISTINCT n.node_type")
    relation_types = _distinct(graph, "MATCH ()-[r]->() RETURN DISTINCT type(r)")
    node_confidence = _distinct(graph, "MATCH (n:Entity) RETURN DISTINCT n.confidence")
    edge_confidence = _distinct(graph, "MATCH ()-[r]->() RETURN DISTINCT r.confidence")
    gxp_classification = _distinct(graph, "MATCH (n:Entity) RETURN DISTINCT n.gxp_classification")
    module = _distinct(graph, "MATCH (n:Entity) RETURN DISTINCT n.module")
    examples = _example_ids_by_node_type(graph)
    examples_text = "\n".join(
        f"  - {node_type}: e.g. {example_id}" for node_type, example_id in examples
    )
    extra_properties = _extra_property_keys(graph)
    extra_properties_block = (
        (
            "\nADDITIONAL PROPERTIES from enrichment scripts (not on every node -- "
            "check `IS NOT NULL` before assuming one applies): "
            f"{', '.join(extra_properties)}. Each enrichment family is namespaced "
            "with its own confidence/source_doc pair, exactly like the base "
            "`confidence`/`source_doc` fields (e.g. `s4_confidence`/`s4_source_doc` "
            "alongside `s4_status`, `disposition_confidence`/`disposition_source_doc` "
            "alongside `disposition_status`) -- always return and cite the matching "
            "sibling field, never state one of these properties as fact without it.\n"
        )
        if extra_properties
        else ""
    )

    return (
        "GRAPH SCHEMA (queried live from the current graph):\n"
        f"- Node labels: {', '.join(node_types)}\n"
        f"- Relationship types: {', '.join(relation_types)}\n"
        f"- Node property `confidence` values: {', '.join(node_confidence)}\n"
        f"- Relationship property `confidence` values: {', '.join(edge_confidence)}\n"
        f"- Node property `gxp_classification` values: {', '.join(gxp_classification)}\n"
        f"- Node property `module` values: {', '.join(module)}\n"
        "\n"
        "NODE_ID CONVENTION — node_id is ALWAYS prefixed by type, never a bare code "
        "(e.g. the business process 'MM01' is node_id 'PROC:MM01', the transaction "
        "'MIGO' is node_id 'TX:MIGO' — never just 'MM01' or 'MIGO'). One real "
        "example per node type in this graph:\n"
        f"{examples_text}\n"
        "\n"
        "NODE PROPERTIES: `node_id`, `node_type`, `label` (the human-readable display "
        "name — there is NO `name` property, use `label`), `module`, "
        "`gxp_classification`, `confidence`, `source_doc`, `source_ref`, `notes`.\n"
        f"{extra_properties_block}"
        "\n"
        "GxP RULES (non-negotiable, from CLAUDE.md):\n"
        "- A node's `confidence` is NEVER 'inferred' — only 'documented', 'peripheral', "
        "or 'gap'.\n"
        "- A relationship's `confidence` is NEVER 'gap' — only 'documented', "
        "'peripheral', or 'inferred'.\n"
        "- Always include `confidence` in your query's RETURN clause so it can be "
        "cited in the answer.\n"
        "- All entities carry the label `Entity` plus their specific node label "
        "(e.g. `:Entity:Transaction`).\n"
    )
