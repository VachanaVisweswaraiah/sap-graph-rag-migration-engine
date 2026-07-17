<!--
NOTE: This file has been sanitized for public/private portfolio use.
Business logic, domain-specific rules, and proprietary details have been masked.
The coding patterns, architecture, and technical implementation remain authentic.
[MASKED] tags indicate where original business logic has been replaced.
-->

# Phase 3, slice 2 — Migration-Mapping Agent + GxP-Compliance Agent

## Context

Slice 1 (foundation + `cypher_guard` + As-Is Query Agent) is implemented and merged. This slice
builds the remaining two agents from `docs/IMPLEMENTATION_PLAN.md` §3.3/§3.5. LangGraph
orchestration (§3.6) and the golden-question eval harness (§3.7), plus all of Phase 4, remain
deferred to follow-up sessions per the user's chosen scope.

**Key architectural decision, different from the As-Is agent:** the As-Is agent necessarily uses
LLM-generated Cypher because it must answer *arbitrary* questions. The Mapping and Compliance
agents do not — their detection/coverage logic is a small, fixed, well-defined query (the plan
even hand-writes the compliance query's skeleton). **Both agents run deterministic, hand-verified
Cypher for their core logic — never LLM-generated** — with an LLM used only as an *optional*
narration layer on top of already-computed facts. Letting an LLM invent the compliance-detection
query itself would reintroduce exactly the risk this project's confidence model exists to guard
against; a missed or malformed detection query in a GxP compliance tool is far worse than an
awkward sentence.

**Real data verified against the live graph before designing anything (not assumed):**
- **`QM:BATCH_RELEASE`'s own `gxp_classification` is blank (`''`)** — despite this node being
  about automatic batch release, a textbook GxP-critical function. If the compliance query
  triggered only on `gxp_classification='GxP-kritisch'`, it would **miss the flagship finding
  entirely**. It is only caught via the edge-confidence branch (`SUSPECTED_SOURCE` is
  `confidence='inferred'`). This is a live confirmation that the plan's OR-combined trigger
  (node `gap` OR edge `inferred` OR target `GxP-kritisch`) is load-bearing, not redundant.
- The compliance query (exact skeleton from `docs/IMPLEMENTATION_PLAN.md` §3.5, run read-only
  against the real graph) returns **8 real paths** today: the flagship `SUSPECTED_SOURCE` edge,
  3 other `inferred` edges (2 `MIGRATES_TO` from Phase 2, 1 original `PRECEDES`/`FOLLOWED_BY`/
  `SUSPECTED_USES_BWA` procurement-sequence guess), 2 gap-touching documented paths (`DOC:FI01`,
  `SOP:QA-016`), and 2 fully-`documented` paths through `GxP-kritisch`
  roles authorizing `PROC:MM01` (context, not gaps — included because the target is GxP-critical,
  correctly low-severity).
- **`MIGRATES_TO` coverage is real but small and module-lopsided**: 2 edges, all in `MM`
  (`TX:MB1C`/`MB1A` → `TX:MIGO`), **zero in `AM`** — not because AM is undocumented (it's
  the *only* fully-documented module) but because AM01's disposition text was a conceptual note
  (a "New Asset Accounting; ledger-decision" note), not a literal code-level redirect, so Phase 2's
  parser correctly extracted nothing. The Mapping Agent's job is to report this honestly (matching
  `docs/IMPLEMENTATION_PLAN.md` §3.3's own example: *"WS1 unmappable — no functional docs"*),
  never to paper over it.
- **`value_stream` still does not exist in the graph** (confirmed again — only in the
  never-graph-loaded `kg_process_master.csv`). Both agents scope by `module` (`MM`/`AM`/`cross`/
  `governance`), same substitution already made for `schema_context.py` in slice 1.
- Transaction node counts by module (denominator for coverage %): `MM`=6, `AM`=10.

## Design

### `src/kgme/agents/mapping.py` (new)

```python
@dataclass(frozen=True)
class ModuleCoverage:
    module: str
    total_transactions: int
    mapped_transactions: int
    mapped_pairs: list[tuple[str, str]]   # (source_node_id, target_node_id)

@dataclass(frozen=True)
class MappingReport:
    coverage: list[ModuleCoverage]
    narrative: str | None   # None unless narration was requested

def compute_mapping_coverage(graph: Graph, *, module: str | None = None) -> list[ModuleCoverage]:
    """Deterministic. For each module (or just the one requested):
        MATCH (n:Entity:Transaction {module: $module})
        OPTIONAL MATCH (n)-[:MIGRATES_TO]->(t)
        RETURN count(DISTINCT n) AS total,
               count(DISTINCT CASE WHEN t IS NOT NULL THEN n END) AS mapped,
               collect(DISTINCT [n.node_id, t.node_id]) AS pairs
    No LLM involved. This is the actual coverage fact."""

def narrate_mapping_report(client: anthropic.Anthropic, *, model: str, coverage: list[ModuleCoverage]) -> str:
    """Optional: one plain LLM call turning the already-computed coverage numbers
    into prose matching the plan's own example style ('WS3/AM01 fully mapped;
    WS2 mapped for MM01 only...' — substituting module for value-stream).
    Explicitly instructed: report only the given numbers, never estimate or
    fill in a module that wasn't in the input data."""

def build_mapping_report(
    graph: Graph, *, module: str | None, narrate: bool, client: anthropic.Anthropic | None, model: str
) -> MappingReport: ...
```

### `src/kgme/agents/compliance.py` (new)

```python
_COMPLIANCE_QUERY = """
MATCH path = (src)-[e]->(t)
WHERE t.gxp_classification = 'GxP-kritisch'
   OR any(n IN nodes(path) WHERE n.confidence IN ['gap'])
   OR any(r IN relationships(path) WHERE r.confidence = 'inferred')
RETURN src.node_id AS source_id, type(e) AS relation, t.node_id AS target_id,
       e.confidence AS edge_confidence, t.gxp_classification AS target_gxp,
       e.source_doc AS source_doc
"""
# Exact skeleton from docs/IMPLEMENTATION_PLAN.md §3.5, verified read-only-safe
# and confirmed to return the real flagship finding against the live graph.

_FLAGSHIP_SOURCE = "QM:BATCH_RELEASE"
_FLAGSHIP_TARGET = "SYS:LAB_SYSTEM"
_FLAGSHIP_RELATION = "SUSPECTED_SOURCE"

@dataclass(frozen=True)
class ComplianceFinding:
    source_id: str
    relation: str
    target_id: str
    edge_confidence: str
    target_gxp: str
    source_doc: str
    is_flagship: bool

@dataclass(frozen=True)
class ComplianceReport:
    findings: list[ComplianceFinding]   # flagship first if present, then ranked
    narrative: str | None

def _severity_tier(finding: ComplianceFinding) -> int:
    """0 = flagship (always first), 1 = inferred edge, 2 = gap-touching
    documented path, 3 = documented path to a GxP-kritisch target (context,
    not a gap). Matches the real tiers found in the live 8-row dry run."""

def run_compliance_scan(graph: Graph) -> list[ComplianceFinding]:
    """Executes _COMPLIANCE_QUERY via read_only_query (never LLM-generated),
    tags is_flagship, sorts by _severity_tier with the flagship pinned first
    regardless of tier logic — a hard-coded pin, not an emergent sort result,
    per CLAUDE.md's explicit 'surface it first' requirement."""

def narrate_compliance_report(client: anthropic.Anthropic, *, model: str, findings: list[ComplianceFinding]) -> str:
    """Optional plain LLM call narrating the already-ranked findings — the
    flagship finding's explanation is anchored in the prompt (RA_PROC01 risk #7,
    automated batch-release decision, unspecified interface) so the model
    contextualizes rather than re-derives it from scratch."""

def build_compliance_report(
    graph: Graph, *, narrate: bool, client: anthropic.Anthropic | None, model: str
) -> ComplianceReport: ...
```

### CLI (`src/kgme/cli.py` additions)

```
kgme map [--module MM|AM|cross|governance] [--narrate]
kgme compliance-scan [--narrate]
```
Both print a structured table by default (module/coverage or ranked findings); `--narrate` adds
an LLM-composed prose summary appended after the structured facts, clearly a second, optional
section — never replacing the deterministic output.

## Tests

- `tests/unit/test_mapping.py`: `_severity_tier`-equivalent-free pure logic — coverage-shape
  assertions on hand-built `ModuleCoverage` inputs (no DB).
- `tests/unit/test_compliance.py`: `_severity_tier` ranking + flagship-pin logic on hand-built
  `ComplianceFinding` lists — assert flagship always sorts first even when given last, assert
  relative ordering of inferred/gap/documented-GxP-kritisch tiers. Pure logic, no DB, no LLM.
- `tests/integration/test_mapping.py`: seed nodes/edges via raw Cypher (self-contained, same
  pattern as `test_disposition.py`) — a module with 2 transactions/1 mapped and a module with
  transactions/0 mapped; assert `compute_mapping_coverage` reports both honestly (not just the
  mapped one).
- `tests/integration/test_compliance.py`: seed a gap node, an inferred edge, and a
  `GxP-kritisch`-target documented edge directly; assert `run_compliance_scan` finds all three
  categories. Separately seed the exact flagship pattern
  (`QM:BATCH_RELEASE -[:SUSPECTED_SOURCE]-> SYS:LAB_SYSTEM`, blank `gxp_classification`, `inferred`)
  alongside unrelated other findings and assert it sorts first regardless of insertion order.
- **Real, live verification** (same discipline as slice 1): `uv run kgme compliance-scan` against
  the actual live graph — confirm the flagship finding is #1 of the real 8, and `uv run kgme map`
  — confirm it honestly reports 2/6 mapped in `MM` and 0/10 mapped in `AM`. If `--narrate` is
  exercised, one real Anthropic API call each, manually inspected for accuracy (not automated).

## Sequencing

1. `agents/mapping.py` core (`compute_mapping_coverage`) + `tests/unit/test_mapping.py` +
   `tests/integration/test_mapping.py` (TDD).
2. `agents/compliance.py` core (`run_compliance_scan`, `_severity_tier`, flagship pin) +
   `tests/unit/test_compliance.py` + `tests/integration/test_compliance.py`.
3. Narration functions (`narrate_mapping_report`, `narrate_compliance_report`) — no new tests
   beyond confirming they're only called when `narrate=True` (covered by the report-builder's own
   unit test with a mocked client).
4. `cli.py`: `kgme map` and `kgme compliance-scan` subcommands + unit tests (mocked, mirroring
   `test_ask_*` in `tests/unit/test_cli.py`).
5. Real live verification against the actual graph and (for `--narrate`) the real Anthropic API.
6. Update `docs/AUDIT.md` with both agents' real findings (the 8-item compliance scan, the 2/6
   vs 0/10 mapping coverage) and the `QM:BATCH_RELEASE` blank-classification discovery.

## Verification

- `uv run pytest tests/unit/test_mapping.py tests/unit/test_compliance.py tests/integration/test_mapping.py tests/integration/test_compliance.py tests/unit/test_cli.py -v` green.
- `make lint` (ruff + mypy strict) clean.
- Full suite still green (`make up && make test`, plus `tests/contract`).
- `uv run kgme compliance-scan` against the real graph: flagship finding is item #1, 8 total
  findings, matching the manual dry-run above exactly.
- `uv run kgme map` against the real graph: `MM` shows 2/6 mapped, `AM` shows 0/10 mapped —
  numbers match the verified real data above.

## Git workflow (same as before, per `CONTRIBUTING.md`)

Fresh feature branch from `main` (the previous PR is merged), commands given at the end once
implementation and verification are complete.
