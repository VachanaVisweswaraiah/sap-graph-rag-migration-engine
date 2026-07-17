<!--
NOTE: This file has been sanitized for public/private portfolio use.
Business logic, domain-specific rules, and proprietary details have been masked.
The coding patterns, architecture, and technical implementation remain authentic.
[MASKED] tags indicate where original business logic has been replaced.
-->

# SAP Simplification Item Catalog enrichment (new feature, post-Phase-1)

## Status: implemented and verified

- 63 tests pass (unit + integration + contract), 95% coverage. `ruff`/`mypy --strict` clean.
- Ran for real against the live 55-node/47-edge graph: **6 nodes matched** (e.g. `TX:MB1C`,
  `TX:MBST`, `TX:AS21`–`TX:AS26`), **18 catalog codes unmatched**, **5 rows skipped**
  (`Program`/`Concept`, no corresponding node type exists yet).
- Confirmed idempotent (re-run produces identical values, zero new nodes) and confirmed the
  original node's `confidence`/`source_doc`/`source_ref` are untouched — only new `s4_*`
  properties were added, with their own independent `s4_confidence='inferred'` provenance.
- New CLI command: `kgme enrich s4-catalog [--file <path>]`.
- `docs/AUDIT.md` created (was a stub) with the real coverage numbers and the deferred-items
  rationale (`Program`/`Concept` rows, Phase 2's separate disposition parser).
- `.claude/hooks/protect-raw-data.sh` extended to also protect `data/external/`.

## Context

The user obtained the official SAP Simplification List for S/4HANA from the public SAP Help
Portal PDF (a legitimate, citable primary source — confirmed not AI-generated) and manually
extracted the rows relevant to NovaPharm Biologics's existing modules (FI-AA, MM-IM, CO-PC-ACT, LE-WM, AP-MD-BP,
LO-BM, CA-GTF-OC, BC-SEC & UI) into a JSON file. They want this used to enrich the knowledge graph
with S/4HANA migration-impact facts (deprecated/replaced transactions, tables, auth objects) so
the future compliance/mapping agents can answer "what breaks and how do I fix it," not just "what
exists today."

This sits **after** Phase 1 (loader — done) and **alongside** Phase 2 (`docs/IMPLEMENTATION_PLAN.md`'s
planned `s4_disposition`-text parser) — it's a second enrichment source, same philosophy, different
input. It is explicitly **not** the "fetch the external SAP Simplification Catalog... deferred by
design" scenario `CLAUDE.md`/`docs/IMPLEMENTATION_PLAN.md` warn against — that deferral was about
an agent *autonomously* going and fetching this data; here the user sourced it manually from a
citable, verifiable location and is handing it to the pipeline explicitly.

**Decisions made via user Q&A:**
1. Represent as **property enrichment** on existing nodes (not new `SimplificationItem` nodes +
   edges) — keeps the graph focused on NovaPharm Biologics's ECC reality per the user's own instinct; can add
   dedicated nodes later if multi-hop impact-analysis queries prove necessary.
2. New properties get **`s4_confidence='inferred'`**, **`s4_source_doc='DERIVED:SAP_SIMPLIFICATION_LIST'`**
   — per `CLAUDE.md` non-negotiable #2 (anything a parser produces is `inferred` until human
   review), applied literally even though the code-matching itself is deterministic string
   equality, not an LLM guess. Same pattern as Phase 2's planned disposition parser.
3. Use the JSON already shared in this conversation as the final dataset (12 rows).

**Finding that revises one earlier answer:** the user approved adding a narrow `Program` node
type for catalog-sourced standard SAP programs. Investigating the real data shows **zero
`Program`-type nodes exist anywhere in the current graph** (custom/standard program discovery is
the deferred ATC-scan dark field) — so there is nothing for a `Program` catalog row to attach to,
and creating a new, disconnected `Program` node purely from the catalog (no edges to anything else
in the graph) contradicts the property-enrichment approach just chosen. **Recommendation: skip
`Program` and `Concept`-type catalog rows from automatic graph writes entirely** — log them
(count + list) as a report for future reference once an ATC scan or deeper process documentation
gives them something to attach to. No node-type-enum change needed at all. (Reviewer: override
this in the plan file before approval if you'd rather have them as standalone nodes anyway.)

Real overlap confirmed by direct check against `data/raw/kg_nodes.csv`: e.g. `MB1C`, `MBST`,
`AS21`-`AS26`, `AB02`, `ABMA` etc. all match existing `TX:*` nodes today.

## Design

### Matching (deterministic, fails closed)

Real node_id prefix-to-type map (verified against the live data):
`TX:` → Transaction/CustomTransaction (20 nodes), `TAB:` → CustomizingTable/SystemTable (3 nodes),
`AUTH:` → AuthorizationObject (8 nodes). Only these three `ecc_object_type` values are matched:

```python
PREFIX_BY_ECC_OBJECT_TYPE: Final[dict[str, str]] = {
    "Transaction": "TX:",
    "Table": "TAB:",
    "Auth_Object": "AUTH:",
}
```

`ecc_object_type` values `Program` and `Concept` are extracted into a separate "unmatched/deferred"
report, never written to the graph.

`ecc_object_name` cells are often multi-value (`"AB01, ABNA, ABMA, ABAW, ABZU, ABZO (non-L posting
transactions)"`) or contain a range token (`"AUN1-AUN11"`). Splitting logic: split on `,`/`;`,
strip parenthetical notes, take the first whitespace-delimited token per part. **Ranges are kept as
the literal token, never expanded** (e.g. `"AUN1-AUN11"` stays one string) — expanding a range is a
guess about SAP's naming convention, which the "fails closed" design explicitly avoids: if it
doesn't exactly match an existing `node_id` suffix, it simply produces no match, which is safe.

For each extracted code, build the candidate `node_id = f"{prefix}{code}"` and check it against the
real `node_id` set (loaded once, not per-row).

### `src/kgme/enrichment/s4_simplification.py` (new)

```python
@dataclass(frozen=True)
class CatalogRow:
    simplification_item_id: str
    ecc_object_type: str
    ecc_object_name: str
    s4hana_status: str
    s4hana_target: str
    sap_note_reference: str
    remediation_category: str
    severity: str
    actionable_recommendation: str

def load_catalog(path: Path) -> list[CatalogRow]: ...
    # json.loads, encoding utf-8 (this file has no BOM requirement like the original CSVs —
    # confirm on the actual file; treat utf-8-sig defensively same as everywhere else)

def extract_codes(ecc_object_name: str) -> list[str]: ...
    # split on [,;], strip "(...)" parenthetical notes, first token per part, dedupe

@dataclass(frozen=True)
class MatchResult:
    matched: list[tuple[CatalogRow, str]]     # (row, matched node_id)
    unmatched_codes: list[tuple[CatalogRow, str]]  # (row, code) — no such node_id in the graph
    skipped_rows: list[CatalogRow]            # ecc_object_type in {Program, Concept}

def match_catalog_to_nodes(rows: Sequence[CatalogRow], existing_node_ids: frozenset[str]) -> MatchResult: ...

def build_enrichment_properties(row: CatalogRow) -> dict[str, str]:
    """s4_status, s4_target, s4_note, s4_severity, s4_remediation_category,
    s4_confidence='inferred', s4_source_doc='DERIVED:SAP_SIMPLIFICATION_LIST',
    s4_source_ref=f"{row.simplification_item_id} (SAP Note {row.sap_note_reference})".
    Deliberately namespaced under s4_* / does NOT touch the node's own top-level
    confidence/source_doc/source_ref — those describe the original ECC documentation
    (e.g. RA_PROC01) and must not be overwritten by this second, independent provenance
    trail."""

def enrich_graph(graph: Graph, rows: Sequence[CatalogRow], logger) -> EnrichmentSummary:
    """1. Load existing node_ids via a single query (MATCH (n:Entity) RETURN n.node_id).
       2. match_catalog_to_nodes(...).
       3. UNWIND-batched: MATCH (n:Entity {node_id: r.node_id}) SET n += r.props
          — idempotent by construction (SET, not MERGE-create); running twice just
          re-sets the same values, no duplication risk to design around.
       4. Log 'enrichment.s4_catalog.completed' with matched/unmatched/skipped counts —
          this IS the audit trail per CLAUDE.md (the log doubles as docs/AUDIT.md's
          future source material).
       5. Return EnrichmentSummary(matched_count, unmatched, skipped) for the CLI to print."""
```

### Storage location for the external file

**Not** `data/raw/` — that directory is the protected, immutable *original hand-off corpus*
(guarded by `.claude/hooks/protect-raw-data.sh`); mixing in a different external reference source
there blurs that boundary. New directory: **`data/external/s4hana_simplification_list.json`**.
Extend `protect-raw-data.sh`'s path match to also cover `data/external/` (same immutability
rationale: it's a downloaded artifact, not something the agent should silently edit) — one-line
change to the existing `case` pattern.

### CLI (`src/kgme/cli.py` addition)

New subcommand, **not** folded into `kgme-load` (this is an occasional, explicit enrichment step
against an external file, not part of the core idempotent Phase-1 load):

```
kgme enrich s4-catalog --file data/external/s4hana_simplification_list.json
```
Prints matched/unmatched/skipped counts and exits 0 always (unmatched codes are expected/normal,
not an error — only a connectivity/schema failure aborts).

### Tests

- `tests/unit/test_s4_simplification.py`: `extract_codes()` against real multi-value/parenthetical/
  range examples from the actual catalog content; `match_catalog_to_nodes()` against a small
  in-memory node_id set (pure, no DB).
- `tests/fixtures/s4_catalog_fixture.json`: ~5 rows — one `Transaction` match against
  `TX:FIX01` (already in `kg_nodes_fixture.csv`), one `Table`/`Auth_Object` row with no matching
  node (must not error, must appear in `unmatched`), one `Program` row and one `Concept` row (must
  appear in `skipped_rows`, never written).
- `tests/integration/test_s4_simplification.py`: load the node fixture, run `enrich_graph`, assert
  the matched node has the 6 new `s4_*` properties with correct values AND that its original
  `confidence`/`source_doc`/`source_ref` are **unchanged**; run `enrich_graph` a second time and
  assert no error and identical resulting property values (idempotency, cheap to prove since it's
  a `SET`).

## Sequencing

1. `data/external/` dir; save the provided JSON there; extend `protect-raw-data.sh`.
2. `src/kgme/enrichment/s4_simplification.py` (load/extract/match/build-properties/enrich_graph).
3. `tests/unit/test_s4_simplification.py` + fixture JSON — pure logic first (TDD).
4. `tests/integration/test_s4_simplification.py` against the real testcontainers FalkorDB.
5. `cli.py`: `kgme enrich s4-catalog` subcommand.
6. Run for real against the live-loaded 55/47 graph; report actual matched/unmatched/skipped
   counts from the real 12-row catalog (not just the fixture) so the user sees genuine coverage.
7. Update `docs/AUDIT.md` (currently a stub) with a short note pointing at this enrichment's log
   events as its audit source, per `CLAUDE.md`'s "the log is the GxP audit trail."

## Verification

- `uv run pytest tests/unit/test_s4_simplification.py tests/integration/test_s4_simplification.py`
  green.
- `uv run kgme enrich s4-catalog --file data/external/s4hana_simplification_list.json` against the
  real loaded graph: report shows matched nodes (expect ones like `TX:MB1C`, `TX:MBST`, `TX:AS21`-
  `TX:AS26` etc. based on the confirmed overlap check), unmatched codes, and skipped
  Program/Concept rows.
- Spot-check in FalkorDB Browser: `MATCH (n:Entity {node_id:"TX:MB1C"}) RETURN n` shows the new
  `s4_status`/`s4_target`/`s4_note` properties alongside the untouched original
  `confidence='documented'`/`source_doc='RA_PROC01'` (or whatever it already was).
- `make lint` (ruff + mypy strict) clean.
