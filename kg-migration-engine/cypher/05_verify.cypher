// NOTE: This file has been sanitized for public/private portfolio use.
// Business logic, domain-specific rules, and proprietary details have been masked.
// The coding patterns, architecture, and technical implementation remain authentic.
// [MASKED] tags indicate where original business logic has been replaced.

// Acceptance assertions for the Phase 1 load. All *_ok columns must be true.
// $expected_nodes/$expected_edges are supplied by db/loader.py from the row counts
// it actually read from kg_nodes.csv/kg_edges.csv — this is a self-consistency
// check (did every row make it into the graph), not a hardcoded dataset size, so
// the same script verifies both the real 55/47 load and small test fixtures.
MATCH (n:Entity) WITH count(n) AS nodes
MATCH ()-[r]->() WITH nodes, count(r) AS edges
OPTIONAL MATCH (bad:Entity) WHERE bad.confidence IS NULL OR bad.source_doc IS NULL
WITH nodes, edges, count(bad) AS nodes_missing_provenance
RETURN
  nodes,
  edges,
  nodes = $expected_nodes AS nodes_ok,
  edges = $expected_edges AS edges_ok,
  nodes_missing_provenance,
  nodes_missing_provenance = 0 AS provenance_ok;
