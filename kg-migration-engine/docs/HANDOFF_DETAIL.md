<!--
NOTE: This file has been sanitized for public/private portfolio use.
Business logic, domain-specific rules, and proprietary details have been masked.
The coding patterns, architecture, and technical implementation remain authentic.
[MASKED] tags indicate where original business logic has been replaced.
-->

# Knowledge Graph Hand-Over — NovaPharm Biologics SAP ECC 6.0 As-Is Process World

**Prepared for:** aiio Data Science team
**Purpose:** Build a knowledge graph of the documented SAP process landscape (modules MM and AM) as a basis for the S/4HANA migration analysis.
**Deliverables covered:** `kg_nodes.csv`, `kg_edges.csv`, `kg_process_master.csv`, `kg_data_dictionary.csv`**Status of the underlying analysis:** Two document batches + two PRD exports processed. Coverage is deliberately uneven (see §6) — this is a *documentation-derived* graph, not a system-derived one.

---

## 1. TL;DR for the busy reader

- The graph is a **Labeled Property Graph** delivered as a normalized node/edge pair, plus one denormalized "one row per process" sheet for eyeballing, plus a data dictionary.
- **55 nodes, 47 edges**, referential integrity verified (no dangling edge endpoints).
- **Every node and every edge carries a** `confidence` **field** with four levels: `documented`, `peripheral`, `inferred`, `gap`. This is the single most important column for your work — it tells you how much weight a fact can bear.
- **Provenance is on every row** (`source_doc` + `source_ref`), so any element can be traced back to a specific document and chapter/page.
- The only places where I went beyond the source documents are the **7** `inferred` **edges** — they are isolated by design so you can include or exclude them with one filter.

---

## 2. Where the data came from (method)

### 2.1 Source corpus

The graph was extracted **exclusively** from the following artifacts. No external SAP knowledge, no assumptions about "how NovaPharm probably does it", nothing from the public internet was used as a fact source. Source short-codes used throughout the CSVs:

| Short-code                                                                    | Artifact                                        | Type                                       | Date               |
|-------------------------------------------------------------------------------|-------------------------------------------------|--------------------------------------------|--------------------|
| `LH_PROC01` / `PH_PROC01` / `RA_PROC01`                                                   | I_ERP-PROC01 "GR without PO reference"               | Requirements / Design Spec / Risk Analysis | 2006 / 2006 / 2007 |
| `LH_AM01` / `PH_AM01`                                                             | I_ERP-AM01 "Asset Accounting Operations"    | Requirements / Design Spec                 | 2005 / 2006        |
| `BP_ZX_MM_BASE`, `BP_ZX_SAMPLE_01/_02/_03`, `BP_ZX_MM_MBST`                           | MM authorization role specs                     | Authorization "Lastenheft"                 | 2014–2025          |
| `BP_ZX_AM01_01/_03/_04`, `BP_ZT_AM01_04`, `BP_ZX_AM_IS`, `BP_ZX_AM_ANZ`, `BP_ZT_AM_ANZ` | AM authorization role specs                     | Authorization "Lastenheft"                 | 2015–2025          |
| `PRD_GP_MM` / `PRD_GP_AM`                                                         | Production process inventory (Technical Places) | System export                              | 09.06.2026         |
| `PRD_T001`                                                                      | Company-code table extract (T001)               | System export                              | current            |
| `SAP_AUFBAU`                                                                    | System landscape diagram                        | Architecture drawing                       | 01/2026            |
| `MODUL_RA_MM`                                                                   | Module-level risk analysis ERP-MM               | **referenced only, not in hand**               | 2006               |
| `FI01`                                                                          | I_ERP-FI01 parallel ledger spec                 | **referenced only, not in hand**               | 2006               |

### 2.2 Extraction logic

1. **Process spine from the PRD inventory.** The two PRD exports define the *ground truth set* of validated business processes (Technical Places). Every `BusinessProcess` node maps to exactly one PRD entry; the `prd_tp_no` carries the Technical-Place number. This is why there are 25 process nodes even though only 2 have deep documentation.
2. **Deep attributes from LH/PH/RA.** For the two fully documented processes (PROC01, AM01) I read requirements (LH), design realization (PH) and — where present — the FMEA risk analysis (RA), and turned each concrete statement into nodes (transactions, movement types, tables, output types, valuation areas …) and edges (uses, controls, produces, reconciles …).
3. **Authorization layer from the role specs.** The authorization "Lastenhefte" yielded role nodes, the role→process `AUTHORIZES` edges, the role-composition `REQUIRES_BASE_ROLE` edges, and the GxP classification per role. They also pinned down org-unit values (sample plants) and, via their change logs, historical org-unit changes — captured as `peripheral` nodes flagged "reconstructed from role history".
4. **Peripheral systems from the landscape diagram.** Anything in `SAP_AUFBAU` (the lab system, MES, label printers, EDI middleware, print-prep servers, document services) became an `ExternalSystem` node at `peripheral` confidence, because a box on an architecture drawing is not the same as a documented interface.
5. **Gaps recorded as first-class objects.** Where a document *references* something that is not in the corpus (the module-level RA, the FI01 spec, the GxP master lists, the SOPs), I created a node at `gap` confidence rather than silently dropping it. This makes the holes queryable.

### 2.3 What I deliberately did NOT do

- I did not invent process flows for the 23 thinly-documented processes. They exist as nodes (so the graph is complete against the PRD), but they have almost no outgoing edges — that absence is itself information.
- I did not normalize away the documentation's own age problem. The LH/PH content is from 2005–2007 (an ECC 5.0 project); only the authorization specs are maintained through 2025. Treat functional facts as "true as of the document date", not "true today" (see §6, gap class).

---

## 3. File-by-file contents

### 3.1 `kg_nodes.csv` — the entities (55 rows)

| Column             | Meaning                                                                                                                                                                                                 |
|--------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `node_id`            | Stable unique ID. **The prefix encodes the type** (`PROC:`, `STEP:`, `TX:`, `BWA:`, `MSG:`, `FORM:`, `TAB:`, `CFG:`, `AREA:`, `ORG:`, `ROLE:`, `AUTH:`, `MD:`, `CLASS:`, `QM:`, `SYS:`, `DOC:`, `SOP:`). Use this as the primary key on import. |
| `node_type`          | The label for your LPG (e.g. `BusinessProcess`, `Transaction`, `CustomTransaction`, `MovementType`, `AuthorizationRole`, `ExternalSystem`, `ReferencedDocument`). Full enum in the dictionary.                        |
| `label`              | Human-readable name.                                                                                                                                                                                    |
| `module`             | `MM`, `AM`, `cross`, or `governance`.                                                                                                                                                                           |
| `gxp_classification` | `GxP-critical`, `non-critical`, `unknown`, or blank. Only populated where a document states it.                                                                                                               |
| `confidence`         | `documented` / `peripheral` / `inferred` / `gap` — see §4.                                                                                                                                                      |
| `source_doc`         | Primary source short-code (table in §2.1).                                                                                                                                                              |
| `source_ref`         | Exact location: chapter, page, or Technical-Place number.                                                                                                                                               |
| `notes`              | Free text, mostly used to spell out the nature of a gap.                                                                                                                                                |

Node-type semantics worth knowing:

- `CustomTransaction` (Z-transactions) vs `Transaction` (SAP standard) — kept as distinct labels so you can color the custom footprint instantly.
- `MovementType` with `notes="Custom (own account determination)"` flags custom BWAs vs standard movement types.
- `ReferencedDocument` / `SOP` at `gap` confidence are the documents NovaPharm's own files point to but which we never received — these are your highest-value retrieval targets.

### 3.2 `kg_edges.csv` — the relationships (47 rows)

| Column                        | Meaning                                                                                 |
|-------------------------------|-------------------------------------------------------------------------------------------|
| `edge_id`                       | Running ID `E0001`…                                                                       |
| `source_id`, `target_id`          | Foreign keys into `kg_nodes.node_id`. Verified: every endpoint exists.                    |
| `relation`                      | Relationship type in `UPPER_SNAKE_CASE`. Directional: read as *source → relation → target*. |
| `confidence`                    | Same four-level scheme as nodes.                                                        |
| `source_doc`, `source_ref`, `notes` | Provenance of the *relationship* (which may differ from the provenance of its endpoints). |

Key relation types and how to read them:

- `HAS_STEP` + `NEXT` — the PROC01 process flow (the only fully modeled flow): `PROC:PROC01 -HAS_STEP-> STEP:* ` and the steps chained by `NEXT`.
- `USES_TRANSACTION`, `USES_MOVEMENT_TYPE`, `PRODUCES_OUTPUT`, `CONTROLLED_BY` — process-to-object links.
- `AUTHORIZES` — role → process. `REQUIRES_BASE_ROLE` — the composite-role pattern (scopes require a base role). `GRANTS_MOVEMENT_TYPE` / `GRANTS_TRANSACTION` / `CONTAINS_OBJECT` / `RESTRICTED_TO` — role internals.
- `LINKED_VIA_INVESTMENT` → `RECONCILES_TO` — the **cross-module chain** investment request → purchase order → logistics invoice verification → asset (AM01). This is the most analytically interesting path in the graph: it connects the otherwise separate MM and AM document worlds and is explicitly documented in `PH_AM01` ch. 3.1.2.
- `SUSPECTED_SOURCE`, `SUSPECTED_USES_BWA`, `PRECEDES` (on procurement) — these are the `inferred` edges (§4.3).

### 3.3 `kg_process_master.csv` — denormalized review sheet (25 rows)

One row per business process with bundled columns (`key_transactions`, `custom_objects`, `movement_types`, `interfaces`, `auth_roles`, `s4_disposition`, `open_gaps`, …). Multi-value cells are `;`-separated.
**Do not load this into the graph** — it duplicates information already in nodes/edges and is lossy. Its job is human QA and a quick management view (e.g. sort by `gxp_evidence_level` to see that only PROC01 has `documented` depth). `s4_disposition` is my migration call per process and is opinion, not source — treat accordingly.

### 3.4 `kg_data_dictionary.csv`

Machine-readable schema: every column, the full enums for `node_type` and `relation`, and the definitions of the four confidence levels. Load this first.

---

## 4. The confidence model (read this twice)

This is the backbone of the whole deliverable. Each level means something specific:

| Level      | Definition                                                                                                                                | How you should treat it                                   |
|------------|-------------------------------------------------------------------------------------------------------------------------------------------|-----------------------------------------------------------|
| `documented` | Stated as a fact or requirement, spelled out in a document we hold.                                                                       | Safe to build on.                                         |
| `peripheral` | Mentioned only in passing — a cross-reference, a box on the landscape diagram, or merely a name in the PRD inventory with no description. | Usable as a hint; verify before relying on it.            |
| `inferred`   | **Derived by me** from documented facts via reasoning. Not stated anywhere.                                                                   | Hypothesis. Review/confirm with NovaPharm SMEs before trusting. |
| `gap`        | The thing is referenced or conceptually required, but its content is **not** in the corpus.                                                   | A hole. These are retrieval/interview targets, not facts. |

Distribution (so you know the shape of the data):

- Nodes: representative majority `documented`, a smaller share `peripheral`, a handful `gap`.
- Edges: representative majority `documented`, a smaller share `peripheral`, 7 `inferred`.

### 4.1 Suggested first query

Color or filter the whole graph by `confidence`. A graph that is mostly `peripheral`/`gap` around a dense `documented` core (PROC01) is exactly what you should see — and it visually reproduces the "1 of 25 processes deeply documented" coverage problem.

### 4.2 The gap nodes

The referenced-but-missing documents: module-level RA ERP-MM, FI01 parallel-ledger spec, the two GxP master lists ("BwA-SAP-ERP", "GxP-critical transactions"), and four SOPs. They are deliberately in the graph so a "what do we still need?" query returns them.

### 4.3 The 7 inferred edges — the only non-sourced content

Isolate them with `WHERE confidence='inferred'`. They are:

1. Procurement sequence (`PRECEDES`) — ordering guessed from process *names*, not documented.
2. Goods receipt → custom movement type (`SUSPECTED_USES_BWA`) — hypothesis that production goods receipt uses the custom BWAs.
3. `QM:BATCH_RELEASE → SYS:LAB_SYSTEM` (`SUSPECTED_SOURCE`) — hypothesis that the automatic batch-release interface (documented as risk #7 in the *referenced* module RA, `RA_PROC01`) is fed by the lab system visible on the landscape diagram. **This is the single most important thing to verify**: it is a GxP-critical automated decision whose interface is completely unspecified in everything we hold.

If your downstream use requires audit-grade strictness, drop all four `inferred`/`gap`-touching elements and you are left with a purely source-backed skeleton.

---

## 5. Loading notes

- Encoding is UTF-8 **with BOM** (Excel-friendly). If your loader dislikes the BOM, strip the first 3 bytes or read as `utf-8-sig`.
- IDs are import-stable and human-readable; safe to use directly as primary keys / MERGE keys.
- Neo4j sketch:

  ```cypher
  LOAD CSV WITH HEADERS FROM 'file:///kg_nodes.csv' AS r
  CALL { WITH r MERGE (n:Entity {id:r.node_id})
         SET n += r } IN TRANSACTIONS;
  LOAD CSV WITH HEADERS FROM 'file:///kg_edges.csv' AS r
  MATCH (s:Entity {id:r.source_id}),(t:Entity {id:r.target_id})
  CALL apoc.create.relationship(s, r.relation, {confidence:r.confidence,
        source_doc:r.source_doc, source_ref:r.source_ref}, t) YIELD rel
  RETURN count(rel);
  ```

   (Promote `node_type` to a real label afterwards with `apoc.create.addLabels` if you want typed nodes.)
- `relation` values are already valid relationship-type identifiers (UPPER_SNAKE, no spaces).
- Treat `kg_process_master.csv` as a separate analytic table, not a graph input.

---

## 6. Known limitations (so they are not discovered the hard way)

1. **Documentation view, not system view.** Custom objects, exits/BAdIs, Z-tables and interface programs are only visible where a document happens to name them. The true Z-footprint is unknown and almost certainly larger. **A repository/ATC scan is required to close this** — it is the #1 recommended next step.
2. **Coverage skew.** 2 of 25 processes are deeply modeled; 23 are node-only. Edge density is therefore concentrated around PROC01/AM01.
3. **Temporal validity.** Functional facts (LH/PH/RA) date to 2005–2007. Only authorization content is maintained to 2025. The graph does not (cannot) tell you which 2006 requirement is still live in 2026.
4. **Leading originals not in hand.** Every document we received is stamped "not a GMP document / for information only" — i.e. these are information copies. The governing GMP originals live in a separate document management system; current version states may differ.

---

## 7. Recommended next steps (my view)

**Immediate (unblocks everything else):**

1. **Commission an ATC / Simplification-Item scan** of the production system and ingest its object list as a new node source (`source_doc='ATC_SCAN'`, confidence `documented`). This converts the §6.1 dark field into data and lets you diff "documented custom objects" vs "actually present custom objects".
2. **Retrieve the gap documents** (module RA ERP-MM, FI01 spec, the two GxP master lists, the four SOPs). Each one will both add nodes and let you *upgrade* existing `peripheral`/`inferred` edges to `documented`.

**Short term (deepen the graph):**
3. Prioritize requirement/design docs for the high-value undocumented processes: **batch tracing** and **batch identification** first — they sit on the GxP traceability axis — then the remaining warehouse-management processes and the EDI-middleware process.
4. Resolve the `inferred` hypotheses with NovaPharm SMEs, especially the **lab system → automatic batch release** interface. Confirmed answers flip `SUSPECTED_*` edges to documented relations.

**Analytic, once enriched:**
5. Run centrality/impact queries to rank S/4HANA migration risk: nodes with many `documented` inbound edges *and* a `gap` neighbor are the dangerous ones (well-used, poorly specified).
6. Use the graph to drive the migration wave plan: GxP-critical subgraph (MM cluster) vs GxP-uncritical subgraph (AM cluster) — the split is already encoded in `gxp_classification` and `module`.
7. Layer a temporal property (document date) onto nodes so "facts older than the ECC-6.0 cutover" can be flagged for re-validation.

**Schema evolution suggestion:** keep `confidence` and `source_doc` as mandatory properties on *every* future node/edge you add. The moment a graph mixes sourced and unsourced facts without that marker, its value for a GxP/audit context collapses. The whole point of this structure is that "we don't know" is represented as explicitly as "we know".

---

*Questions on any node, edge, or classification: each row is traceable via* `source_doc` *+* `source_ref` *back to the exact document location. If you want this re-emitted in a specific target format (Neo4j dump, RDF/Turtle, GraphML, TigerGraph), that conversion is straightforward from the node/edge pair.*
