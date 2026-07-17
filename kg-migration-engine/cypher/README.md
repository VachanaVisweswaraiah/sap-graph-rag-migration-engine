<!--
NOTE: This file has been sanitized for public/private portfolio use.
Business logic, domain-specific rules, and proprietary details have been masked.
The coding patterns, architecture, and technical implementation remain authentic.
[MASKED] tags indicate where original business logic has been replaced.
-->

# cypher/

Only `05_verify.cypher` lives here as a static file — it's a single aggregate query with
no per-row dynamic-type concern.

Steps 01–04 (constraints, node load, edge load, label promotion) are **not** static `.cypher`
files. They're generated/executed directly in `src/kgme/db/loader.py`:

- **Constraints** (was `01_constraints.cypher`): FalkorDB's Python client exposes
  `Graph.create_node_unique_constraint("Entity", "node_id")` natively (it also creates the
  required range index) — no need to hand-write `GRAPH.CONSTRAINT CREATE` text.
- **Node load** (was `02_load_nodes.cypher`, `LOAD CSV`): `kg_nodes.csv` is read directly in
  Python (`encoding="utf-8-sig"`) and loaded via a parameterized
  `UNWIND $rows AS r MERGE (n:Entity {node_id: r.node_id}) SET n += r.props` batch — no file
  needs to be mounted into the FalkorDB container.
- **Edge load** (was `03_load_edges.cypher`, `apoc.merge.relationship`): FalkorDB has no APOC,
  and plain Cypher can't parameterize a relationship type. `kg_edges.csv` rows are grouped by
  `relation` (validated against `kg_data_dictionary.csv` by `db/schema.py`), and one
  `MERGE (s)-[rel:<RELATION_LITERAL> {edge_id: r.edge_id}]->(t)` batch runs per relation group.
- **Label promotion** (was `04_promote_labels.cypher`, `apoc.create.addLabels`): same grouping
  pattern over `node_type`.

See `docs/plans/phase1-restructuring.md` (v6) for the full rationale.
