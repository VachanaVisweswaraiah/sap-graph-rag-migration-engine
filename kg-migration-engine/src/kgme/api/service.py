"""Shared aggregation logic used by both the JSON API (GET /module/{module}/impact)
and the dashboard's module-impact view — a single source of truth so the two never
drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass

from falkordb import Graph

from kgme.agents.mapping import ModuleCoverage, compute_mapping_coverage

_MODULE_NODE_COUNTS_QUERY = """
MATCH (n) WHERE n.module = $module
RETURN count(n) AS total,
       count(CASE WHEN n.confidence = 'gap' THEN 1 END) AS gap,
       count(CASE WHEN n.confidence = 'peripheral' THEN 1 END) AS peripheral,
       count(CASE WHEN n.confidence = 'documented' THEN 1 END) AS documented
"""

# Counts nodes the S/4 Simplification Catalog enrichment (enrichment/s4_simplification.py)
# has flagged, grouped by whatever severity string the catalog actually used -- not a
# hardcoded set of severities, since the catalog defines more (e.g. "Hard Stop (Conversion
# will fail)", "Optional") than any single module's matched nodes happen to have hit so far.
_MODULE_S4_SEVERITY_COUNTS_QUERY = """
MATCH (n) WHERE n.module = $module AND n.s4_severity IS NOT NULL
RETURN n.s4_severity AS severity, count(n) AS count
"""


@dataclass(frozen=True)
class ModuleImpact:
    module: str
    total_nodes: int
    gap_nodes: int
    peripheral_nodes: int
    documented_nodes: int
    mapping_coverage: ModuleCoverage
    s4_flagged_nodes: dict[str, int]


def compute_module_impact(graph: Graph, module: str) -> ModuleImpact:
    """Combines a fixed node-count-by-confidence query with
    agents.mapping.compute_mapping_coverage — reused verbatim, not re-derived.
    s4_flagged_nodes is empty for every module until `kgme enrich s4-catalog` has been
    run — it's not an error, just means no catalog rows have been matched yet."""
    result = graph.ro_query(_MODULE_NODE_COUNTS_QUERY, {"module": module})
    total, gap, peripheral, documented = result.result_set[0]
    coverage = compute_mapping_coverage(graph, module=module)[0]
    s4_result = graph.ro_query(_MODULE_S4_SEVERITY_COUNTS_QUERY, {"module": module})
    s4_flagged_nodes = {severity: count for severity, count in s4_result.result_set}
    return ModuleImpact(
        module=module,
        total_nodes=total,
        gap_nodes=gap,
        peripheral_nodes=peripheral,
        documented_nodes=documented,
        mapping_coverage=coverage,
        s4_flagged_nodes=s4_flagged_nodes,
    )
