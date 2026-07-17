<!--
NOTE: This file has been sanitized for public/private portfolio use.
Business logic, domain-specific rules, and proprietary details have been masked.
The coding patterns, architecture, and technical implementation remain authentic.
[MASKED] tags indicate where original business logic has been replaced.
-->

# CLAUDE.md — Value-Stream GraphRAG Migration Engine

SAP ECC 6.0 → S/4HANA **as-is knowledge graph** + GraphRAG layer for a **GxP-regulated**
pharma migration (NovaPharm Biologics). FalkorDB + Python. Build spec: `docs/IMPLEMENTATION_PLAN.md`.
Prior-conversation context: `docs/HANDOFF.md`. Restructuring/graph-DB-choice history:
`docs/plans/phase1-restructuring.md`.

## Non-negotiables (GxP)
1. **Provenance on everything.** Every node/edge carries `confidence`, `source_doc`, `source_ref`. No exceptions.
2. **LLM/derived output is NEVER `documented`.** Anything a parser or agent produces is `confidence='inferred'`
   with a `DERIVED:` prefix in `source_doc`. Only a human review step promotes a fact to `documented`.
3. **Reproducible builds.** Idempotent loads, pinned deps (`uv.lock` committed), no manual DB edits.

## Hard rules
- Load graph **only** from `kg_nodes.csv` + `kg_edges.csv`. **Never graph-load `kg_process_master.csv`** — it is an analytics table.
- Relationships load via `MERGE` keyed on `edge_id`, grouped by relation type (relationship types are literal Cypher, not parameterizable — see `db/schema.py`/`db/loader.py`). Every relation type and node type used in a write must be validated against `kg_data_dictionary.csv`'s enum first — **never interpolate an unvalidated string into a label or relationship-type position.**
- Read all CSVs as **`utf-8-sig`** (source files have a UTF-8 BOM).
- Agent DB access is **read-only**, enforced in two layers: a scoped Redis ACL user (`+@read`, `+GRAPH.RO_QUERY` only) at the DB layer, plus `cypher_guard`'s static validator at the app layer. NL→Cypher must pass `cypher_guard`; a write-capable query in an agent path is a defect, not a nit.
- Compliance logic keys off **node** `confidence ∈ {gap}` and **edge** `confidence = inferred`. (No edge is ever `gap`; no node is ever `inferred`.)
- Segmentation is **value stream + cross-section**, never migration "waves" (waves are not in the data).
- Flagship compliance finding: the `SUSPECTED_SOURCE` edge `QM:BATCH_RELEASE → SYS:LAB_SYSTEM` (RA_PROC01 risk #7). Surface it first.

## Commands
- `make up` / `make down` — FalkorDB (Docker); Browser UI at http://localhost:3000
- `make load` — build the graph (`uv run kgme-load`)
- `make test` — pytest (unit + testcontainers-based integration)
- `make lint` — ruff + mypy(strict)
- `uv sync` — install/refresh deps

## Working agreement
- Follow `docs/IMPLEMENTATION_PLAN.md` **phase by phase**; do not start a phase before the previous DoD is met.
- TDD: write the failing test first (esp. loader idempotency, cypher_guard, disposition parser), then code.
- For current API signatures (LangGraph, falkordb-py, Anthropic SDK, FastAPI) use the **Context7** MCP — do not rely on memorized signatures.
- Do **not** fetch the external SAP Simplification Catalog or add a `Program`/`CustomObject` node type yet — both are deferred by design.
