<!--
NOTE: This file has been sanitized for public/private portfolio use.
Business logic, domain-specific rules, and proprietary details have been masked.
The coding patterns, architecture, and technical implementation remain authentic.
[MASKED] tags indicate where original business logic has been replaced.
-->

# Phase 2 — Disposition enrichment (docs/IMPLEMENTATION_PLAN.md §2)

## Status: implemented and verified

- 100 tests pass (unit + integration + contract), 96% coverage. `ruff`/`mypy --strict` clean.
- Ran for real against the live graph (post-Phase-1, post-catalog-enrichment): **3 `MIGRATES_TO`
  edges** (`TX:MB1C`/`TX:MB1A`/`TX:MB1B` → `TX:MIGO`), **9 `disposition_status` properties**,
  **0** unmatched, **18** unparsed — exactly matching the design's predicted real yield.
  Confirmed idempotent on a second run (identical counts, no duplicate edges/nodes).
- Confirmed `s4_*` (catalog) and `disposition_*` (this parser) namespaces coexist without
  collision on shared nodes (e.g. `TX:MB1C` and `TX:MB02`).
- **Known limitation found and documented** (not fixed, low impact): `TX:MIGO` receives two
  different status-only facts (`"central"` from its own notes, `"unchanged"` from `MM02`'s
  disposition) — `disposition_status` being a single property means the second write wins
  deterministically rather than both being retained/flagged as a collision.
- New CLI command: `kgme enrich disposition [--nodes-file ...] [--process-master-file ...]`.
- `docs/AUDIT.md` updated with this enrichment's real coverage and the limitation above.

## Context

This is the next item in the original build spec, sitting alongside (not replacing) the
already-shipped SAP Simplification Catalog enrichment (`src/kgme/enrichment/s4_simplification.py`).
Where that module derives facts from an *external* SAP document, Phase 2 derives facts from text
NovaPharm Biologics's own hand-curated data already contains:

1. `kg_process_master.csv`'s `s4_disposition` column (25 process-level rows, e.g.
   `"Zwangsumbau (MB1C->MIGO) + Revalidierung"`) — per `HANDOFF_DETAIL.md` §3.3, this text is
   itself "my migration call per process... opinion, not source," so parsing it is still
   deriving inferred content, not restating a primary fact.
2. `kg_nodes.csv`'s own `notes` field on `Transaction`/`CustomTransaction` nodes (e.g.
   `TX:MB1C notes="S/4: abgeschaltet -> MIGO/Fiori"`) — the exact example `CLAUDE.md`'s original
   authors had in mind.

**Real patterns found by direct inspection of both files** (not assumed — verified):

From `kg_nodes.csv.notes` (only cells starting with `"S/4:"` are in scope):
- `"S/4: abgeschaltet -> TARGET[/extra]"` (1 row: `TX:MB1C` → `MIGO`) → a migration edge.
- `"S/4: abgeschaltet"` alone, no arrow (7 rows: `MB1A`, `MB1B`, `MB02`, `MBST`, `MBRL`, `MBSL`,
  `MBSU`) → deprecated with no known successor — property only.
- `"S/4: zentrale TA"` (1 row: `MIGO`) → this node is the consolidation target — property only.
- Everything else in `notes` (`"Spec fehlt"`, `"gap"`, `"Sunset-Kandidat"`, etc.) doesn't start
  with `"S/4:"` — out of scope for this parser, untouched.

From `kg_process_master.csv.s4_disposition` (only cells starting with `"Zwangsumbau"` are in scope):
- `"Zwangsumbau (SOURCE->TARGET)"` literal (1 row: `MM01`, `MB1C->MIGO`) → a migration edge.
- `"Zwangsumbau (PREFIX*->TARGET)"` wildcard (5 rows: `MM03/05/07/08/12`, all `"MB1*->MIGO"`) —
  verified the *same row's* `key_transactions` column literally lists the matching codes
  (`"(MB1A/MB1B/MBRL via Basisrolle)"`); resolving `MB1*` against it deterministically yields
  `MB1A`, `MB1B` (`MBRL` correctly excluded — doesn't start with `"MB1"`). Bounded to that row's
  own data, not a corpus-wide guess.
- `"Zwangsumbau (X bleibt)"` (1 row: `MM02`, `"MIGO bleibt"`) → target `X` is unchanged — property
  only.
- `"Zwangsumbau (<conceptual note>)"` with no `->` and no `"bleibt"` (1 row: `AM01`,
  `"New Asset Accounting; Ledger-Entscheidung"`) → not code-level, unparseable.
- `"offen (Doku fehlt)"` (15 rows) and `"Synergie (...)"` (1 row: `MM26`) → unparseable.

Net real yield: **3 distinct `MIGRATES_TO` edges** (`TX:MB1C`/`TX:MB1A`/`TX:MB1B` → `TX:MIGO`, all
verified to already exist as real nodes — no orphan-node problem this time), a handful of
property-only markers, and ~17 rows correctly logged as unparseable rather than guessed at. This
matches the DoD in `docs/IMPLEMENTATION_PLAN.md` §2.2: *"unhandled patterns are reported, not
invented."*

**Decisions made via user Q&A:**
1. New properties use a **`disposition_*` namespace** (`disposition_status`,
   `disposition_source_doc`, `disposition_source_ref`, `disposition_confidence='inferred'`) —
   kept fully separate from the catalog enrichment's `s4_*` namespace, since they're independent
   provenance trails from different sources that happen to sometimes land on the same node (e.g.
   `TX:MB1C` gets both `s4_status='Deprecated'` from the catalog *and*
   `disposition_status`/a `MIGRATES_TO` edge from this parser) — a future agent can cross-check
   them rather than one silently overwriting the other.
2. **Wildcard resolution is in scope**, resolved deterministically against the same row's
   `key_transactions` column.
3. **No `DEPRECATED_BY` relation type** — the real data never expresses "deprecated by X" (reverse
   direction); every case that has a known successor is naturally a `MIGRATES_TO` edge, and every
   case without one is a property-only `disposition_status='deprecated'` marker. Only
   `MIGRATES_TO` is introduced as a new relation type.

**Verified during design (not assumed):** `key_transactions` cells use `/` and `;` as separators
and are sometimes wrapped in one layer of parentheses with a trailing descriptive note (e.g.
`"(MB1A/MB1B/MBRL via Basisrolle)"`) — this is a **different convention** from
`s4_simplification.py`'s `extract_codes()` (which splits on `,`/`;` only, and deliberately does
**not** split on `/` — that's what makes it correctly refuse to guess at `"XD01/02/03"`-style
compound tokens). Reusing `extract_codes` directly was tried and confirmed to silently return
`[]` for the wrapped/`/`-separated `key_transactions` format. **Do not modify
`s4_simplification.py`** to accommodate this (it would change already-shipped, already-run
behavior) — write a small dedicated `extract_key_transaction_codes()` in the new module instead,
same shape-filter concept, different separator handling.

## Design

### `src/kgme/enrichment/disposition.py` (new)

```python
_DISPOSITION_CODE_SHAPE = re.compile(r"^[A-Z][A-Z0-9_]{1,29}$")

@dataclass(frozen=True)
class DispositionFact:
    kind: Literal["migrates_to", "status_only"]
    source_node_id: str          # e.g. "TX:MB1C" — always a node_id we will verify exists
    target_node_id: str | None   # e.g. "TX:MIGO" — set only for kind="migrates_to"
    status: str | None           # "deprecated" | "unchanged" | "central" — set only for status_only
    source_ref: str              # e.g. "kg_nodes.csv:TX:MB1C.notes" or "kg_process_master.csv:MM03.s4_disposition"
    raw_text: str                # original cell text, kept for the audit trail

@dataclass(frozen=True)
class UnparsedEntry:
    source_ref: str
    raw_text: str

def extract_key_transaction_codes(cell: str) -> list[str]: ...
    # strips one wrapping paren layer if present, splits on [;/], keeps only
    # SAP-code-shaped tokens (same shape regex concept as s4_simplification.py,
    # separate function — see "Verified during design" note above for why)

def parse_node_notes(node_id: str, notes: str) -> DispositionFact | UnparsedEntry | None:
    """None if notes doesn't start with "S/4:" (out of scope, not even worth reporting
    as unparsed — the overwhelming majority of notes cells are about something else
    entirely, e.g. "Spec fehlt"). Otherwise matches abgeschaltet-with-target /
    abgeschaltet-alone / zentrale TA, else returns UnparsedEntry."""

def parse_process_disposition(
    process_id: str, disposition: str, key_transactions: str
) -> list[DispositionFact] | UnparsedEntry:
    """None-equivalent skip if disposition doesn't start with "Zwangsumbau" (covers
    offen/Synergie rows without flagging them as parse failures — they're not
    Zwangsumbau-shaped at all). Otherwise matches literal arrow / wildcard arrow
    (resolved via extract_key_transaction_codes, filtered by prefix) / "X bleibt",
    else returns UnparsedEntry. Wildcard resolution can yield 0-N DispositionFacts
    from one row (0 if no key_transactions code matches the prefix — logged, not
    an error)."""

def load_dispositions(
    nodes_path: Path, process_master_path: Path
) -> tuple[list[DispositionFact], list[UnparsedEntry]]:
    """Reads both CSVs (utf-8-sig, via kgme.db.loader.read_csv_rows — reused, not
    duplicated), runs both parsers over every row, returns the combined facts and
    unparsed entries. Never raises on an unparsed row — only structurally missing
    columns would raise (a real error, not a parse gap)."""

@dataclass(frozen=True)
class DispositionSummary:
    edges_written: int
    properties_written: int
    unmatched_targets: list[DispositionFact]  # source or target node_id doesn't exist in graph
    unparsed: list[UnparsedEntry]

def apply_dispositions(
    graph: Graph, facts: Sequence[DispositionFact], logger: structlog.stdlib.BoundLogger
) -> DispositionSummary:
    """1. Load existing node_ids (MATCH (n:Entity) RETURN n.node_id) — same pattern as
          s4_simplification.enrich_graph.
       2. For kind="migrates_to" facts: keep only those where BOTH source_node_id and
          target_node_id are in existing_node_ids (fails closed — never creates a node);
          UNWIND-batched:
            MATCH (s:Entity {node_id: r.source_id}) MATCH (t:Entity {node_id: r.target_id})
            MERGE (s)-[rel:MIGRATES_TO]->(t)
            SET rel.confidence='inferred', rel.source_doc='DERIVED:s4_disposition',
                rel.source_ref=r.source_ref, rel.notes=r.raw_text
          MERGE keyed on the (source, target, MIGRATES_TO) pattern itself — no synthetic
          edge_id needed, since re-stating "MB1A migrates to MIGO" from multiple process
          rows should collapse to one edge, not create duplicates or need dedup logic.
       3. For kind="status_only" facts: keep only those where source_node_id exists;
          UNWIND-batched:
            MATCH (n:Entity {node_id: r.node_id})
            SET n.disposition_status=r.status, n.disposition_confidence='inferred',
                n.disposition_source_doc='DERIVED:s4_disposition', n.disposition_source_ref=r.source_ref
          Never touches confidence/source_doc/source_ref or s4_* — same namespacing
          discipline as s4_simplification.py.
       4. Log 'enrichment.disposition.completed' with counts.
       5. Return DispositionSummary."""
```

### CLI (`src/kgme/cli.py` addition)

Mirrors the existing `kgme enrich s4-catalog` subcommand exactly:
```
kgme enrich disposition [--nodes-file data/raw/kg_nodes.csv] [--process-master-file data/raw/kg_process_master.csv]
```
Defaults point at the real files. Prints edges/properties-written counts plus unmatched-target and
unparsed counts. Exits 0 always (unparsed/unmatched are expected, not errors).

### Tests

- `tests/unit/test_disposition.py`: parse functions tested against the **actual real strings**
  found by inspection (not synthetic-only) — e.g. `parse_node_notes("TX:MB1C", "S/4: abgeschaltet -> MIGO/Fiori")`
  must yield a `migrates_to` fact targeting `TX:MIGO` (not `TX:MIGO/Fiori`); `extract_key_transaction_codes("(MB1A/MB1B/MBRL via Basisrolle)")`
  must yield exactly `["MB1A", "MB1B", "MBRL"]` (filtering by wildcard prefix happens in
  `parse_process_disposition`, not in the extractor); the `AM01`/`offen`/`Synergie` rows must
  produce `UnparsedEntry`, never a guessed fact. Golden-case style per `CLAUDE.md`'s working
  agreement ("write the failing test first... esp. disposition parser").
- `tests/integration/test_disposition.py`: does **not** reuse the shared
  `tests/fixtures/kg_nodes_fixture.csv` (touching it would break Phase 1's exact-count
  assertions in `test_loader.py`) — instead writes 2-3 nodes directly via a raw `graph.query(...)`
  MERGE inside the test itself (self-contained, no shared-fixture coupling), then calls
  `apply_dispositions` with hand-built `DispositionFact` objects and asserts: edge created with
  correct provenance, idempotent on a second call (no duplicate edge), a fact targeting a
  non-existent node is reported in `unmatched_targets` and never creates a node, and the source
  node's original `confidence`/`source_doc` are untouched.

## Sequencing

1. `src/kgme/enrichment/disposition.py` — `extract_key_transaction_codes`, `parse_node_notes`,
   `parse_process_disposition`, `load_dispositions` (pure logic first, TDD).
2. `tests/unit/test_disposition.py` against real strings from the actual data files.
3. `apply_dispositions` + `tests/integration/test_disposition.py` against a real testcontainers
   FalkorDB.
4. `cli.py`: `kgme enrich disposition` subcommand + unit tests (mocked, mirroring
   `test_enrich_s4_catalog_*` in `tests/unit/test_cli.py`).
5. Run for real against the live 55/47 (+ already-enriched) graph; confirm the 3 expected
   `MIGRATES_TO` edges and the property markers appear, and that `s4_status`/`s4_*` properties
   from the earlier catalog enrichment are untouched.
6. Update `docs/AUDIT.md` with this enrichment's real coverage numbers, same pattern as the
   catalog enrichment's entry.

## Verification

- `uv run pytest tests/unit/test_disposition.py tests/integration/test_disposition.py -v` green.
- `make lint` (ruff + mypy strict) clean.
- Full suite (`uv run pytest tests/unit tests/integration tests/contract --cov=kgme --cov-fail-under=70`)
  still green — confirms nothing in Phase 1 or the catalog enrichment regressed.
- `uv run kgme enrich disposition` against the real live graph: report shows 3 edges written
  (`TX:MB1C/MB1A/MB1B → TX:MIGO`), the deprecated/unchanged/central property markers, and ~17
  unparsed entries logged (matching the real `offen`/`Synergie`/conceptual-AM01 rows).
- Spot-check in FalkorDB Browser:
  `MATCH p=(:Entity {node_id:"TX:MB1C"})-[:MIGRATES_TO]->(:Entity {node_id:"TX:MIGO"}) RETURN p`
  renders the edge; `TX:MB1C`'s `confidence`/`source_doc` (from Phase 1) and `s4_status` (from the
  catalog enrichment) remain exactly as they were before this run.

## After this plan is approved and implemented — git workflow

<!-- [MASKED] Internal business context removed -->

```bash
git status                                   # confirm what changed
git add src/kgme/enrichment/disposition.py src/kgme/cli.py \
        tests/unit/test_disposition.py tests/unit/test_cli.py \
        tests/integration/test_disposition.py docs/AUDIT.md \
        docs/plans/phase2-disposition-enrichment.md
git commit -m "kg: add Phase 2 disposition parser (MIGRATES_TO edges + disposition_* properties)"
make lint && make test-unit                  # required before push, per CONTRIBUTING.md §3
make up && make test                         # also required: this touches src/kgme/enrichment/
git push -u origin <branch>                  # -u already set from the existing branch; updates the open PR
```
No `gh pr create` needed — the PR already exists and will pick up the new commit automatically.
