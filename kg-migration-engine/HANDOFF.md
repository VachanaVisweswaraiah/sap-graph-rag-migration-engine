<!--
NOTE: This file has been sanitized for public/private portfolio use.
Business logic, domain-specific rules, and proprietary details have been masked.
The coding patterns, architecture, and technical implementation remain authentic.
[MASKED] tags indicate where original business logic has been replaced.
-->

# HANDOFF — carried over from planning conversation

This document catches Claude Code up on everything decided before implementation started.
Authoritative build spec is `docs/IMPLEMENTATION_PLAN.md`; this is the "why" and current state.
For the full data extraction methodology, source corpus, and confidence-model rationale behind
the numbers below, see `docs/HANDOFF_DETAIL.md`.

## What this project is
An **as-is knowledge graph** of NovaPharm Biologics's SAP MM/AM landscape, plus a GraphRAG query/agent
layer, to support and de-risk the **ECC 6.0 → S/4HANA** migration in a **GxP-regulated** context.
The graph is deliberately honest about what is *documented* vs *unknown* — the gaps are the product.

## Data reality (verified file-by-file — do not re-litigate)
- **55 nodes / 47 edges** in `kg_nodes.csv` / `kg_edges.csv`. `kg_process_master.csv` is a
  denormalized **analytics table**, not a graph input. `kg_data_dictionary.csv` documents the schema.
- **Segmentation = 3 value streams + 4 cross-sections** (already encoded in `value_stream` / `module`):
  - WS1 Procurement (Procure-to-Pay) — thinnest; **no LH/PH/RA** for any of its 7 processes.
  - WS2 Inventory & Production Logistics (incl. WM, Batch/QM, Output) — best documented, but only PROC01 of 17 is deep.
  - WS3 Assets & Finance (FI-AA) — AM01 only; the sole fully-covered stream.
  - Cross-sections A Batch/GxP · B Output/Forms · C Custom-Objects · D Validation/Governance → treat as **filters/tags**, not phases.
- **There is NO migration "wave" structure in the corpus.** The pre-project/Wave 1/Wave 2 scheme from an
  earlier draft plan was invented. If a real wave plan exists, it is an **external overlay to import later**.
- **Confidence values** — nodes: `documented`(38) / `peripheral`(14) / `gap`(3). edges: `documented`(33) / `peripheral`(9) / `inferred`(5).
  **No edge is ever `gap`; no node is ever `inferred`.** Compliance logic must key off the correct side.
- **S/4 dispositions already exist** as hand-curated text in `kg_process_master.s4_disposition` and node `notes`
  (e.g. ZX_SAMPLE_01 → MIGO/Fiori). Parse these first; do **not** fetch an external catalog as the primary source.
- **No `Program`/`CustomObject` node type yet** — custom-object footprint is incomplete until an ATC scan (deferred).
- External systems in the landscape (Lab System, MES, Zebra, SampleVendor, xSuite, Business Connector, and other
  sample external systems) match the `SYS:`/`ExternalSystem` nodes exactly.

## Flagship finding (highest-value item in the whole graph)
The inferred `SUSPECTED_SOURCE` edge **`QM:BATCH_RELEASE → SYS:LAB_SYSTEM`** = RA_PROC01 risk #7:
an **automatic batch-release decision** driven by an **unspecified interface**. High centrality + low
confidence + GxP-critical. The compliance agent must surface this first; resolving who owns that interface
is the top question for the NovaPharm SMEs.

## Key engineering fixes already decided
- Graph DB is **FalkorDB**, not Neo4j (pivoted during Phase 1 — see `docs/plans/phase1-restructuring.md`
  v5 for the full rationale: no APOC needed, native `MERGE` on nodes+relationships, source-available
  licensing, stronger read-only enforcement via Redis ACL).
- Idempotent relationship load via `MERGE` keyed on `edge_id`, grouped by relation-type literal
  (FalkorDB has no APOC and plain Cypher can't parameterize a relationship type — see `cypher/README.md`).
- Read CSVs as `utf-8-sig` (both have a UTF-8 BOM).
- Uniqueness constraint on `node_id`; promote `node_type` to a real graph label.
- Read-only Cypher guardrail for the NL→Cypher agent (Redis ACL read-only user + `cypher_guard` static
  write-clause rejector).
- Corrected compliance trigger (walks node `gap` / edge `inferred`, not edge `gap`).

## Build sequence (see plan for DoD per phase)
0 Scaffolding → 1 Graph core (**done** — see `docs/plans/phase1-restructuring.md`) → 2 Disposition enrichment →
3 Agents (As-Is / Mapping / GxP-Compliance) → 4 FastAPI + dashboard (tech TBD, **not Streamlit**).

## Deferred — needs external input, NOT code
- ATC/repository scan (introduces `Program`/`CustomObject`, closes the custom-object dark field).
- Retrieval of the remaining `gap` documents (module-RA ERP-MM, FI01 spec, GxP master lists, SOPs) → upgrades `inferred`→`documented`.
- Optional SAP Simplification Item Catalog cross-check (validate, don't overwrite, the parsed dispositions).
- Optional wave overlay, if a real one exists.

## Open questions for the manager / SMEs
1. Does a real migration wave/phase plan exist outside this corpus?
2. ATC scan vs. gap-document retrieval — which is funded first?
3. Who owns the Lab System → automatic batch-release interface, and is there an interface spec in the DMS?
