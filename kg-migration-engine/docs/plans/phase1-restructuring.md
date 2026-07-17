<!--
NOTE: This file has been sanitized for public/private portfolio use.
Business logic, domain-specific rules, and proprietary details have been masked.
The coding patterns, architecture, and technical implementation remain authentic.
[MASKED] tags indicate where original business logic has been replaced.
-->

# Production-grade restructure + Phase 1 loader (kg-migration-engine)

## Status: implemented and verified (v7)

Phase 1's Definition of Done is met against real FalkorDB (via docker-compose and separately via
testcontainers): `make up && make load && make lint && make test` all green.
- `make load` (`uv run kgme-load`) against the real `data/raw` CSVs: `nodes_ok=true edges_ok=true
  provenance_ok=true`, exactly 55 nodes / 47 edges.
- Re-running `make load` a second time is idempotent (confirmed 0 duplicate edges on the real
  dataset, plus a dedicated fixture-based idempotency test).
- `uv run kgme db check --deep`: all 4 checks OK against a freshly loaded graph.
- FalkorDB Browser reachable at `http://localhost:3000` (HTTP 200).
- 45 tests pass (unit + integration + contract), 94% coverage (gate is 70%).
- `ruff check`, `ruff format --check`, `mypy --strict` all clean.

Remaining known gaps, intentionally out of this plan's scope: the Redis ACL read-only user
(commented out in `docker-compose.yml`, activates with Phase 3's `cypher_guard`); `docs/DATA_MODEL.md`,
`docs/AUDIT.md`, `docs/OBSERVABILITY.md`, `docs/TESTING.md` are still stubs-to-be-written, not
required for Phase 1's DoD. Phases 2-4 (disposition enrichment, agents, API/dashboard) are
unstarted placeholders per `docs/IMPLEMENTATION_PLAN.md`.

## Version history

- **v1** — initial plan (Neo4j-based; sections A–F, sequencing, verification).
- **v2** — corrections: `03_load_edges` critical/aborting; Python `utf-8-sig` prepare step;
  testcontainer APOC env vars; `main_load` catches `LoadAbortedError`.
- **v3** — relocated plan into `docs/plans/phase1-restructuring.md` (git-tracked).
- **v4** — docs/ cleanup: deleted empty stray `docs/PHASE1_RESTRUCTURE_PLAN.md`; renamed
  `docs/INITIAL_PLAN.md` → `docs/HANDOFF_DETAIL.md`, cross-linked from `HANDOFF.md`.
- **v7 (current) — three real FalkorDB behaviors discovered while running `db/loader.py` against
  a live testcontainer, all fixed in code (not just documented):**
  1. **Trailing `;` + trailing whitespace/newline breaks single-statement parsing.** FalkorDB
     rejects a query with `Error: query with more than one statement is not supported` if the
     Cypher text ends in `;` followed by *anything* (even just a trailing newline) — a bare `;`
     with nothing after it is fine. `cypher/05_verify.cypher` is read from disk with a trailing
     newline, so `db/loader.py._verify_step` now strips trailing whitespace and a trailing `;`
     via a new `_strip_trailing_semicolon()` helper before sending any `.cypher` file's contents
     to `graph.query()`/`graph.ro_query()`.
  2. **`Graph.create_node_unique_constraint` is not idempotent.** A second call on an
     already-constrained label/property raises `Constraint already exists` instead of no-op'ing —
     this broke the idempotency test on a second `load_graph()` run. `_constraints_step` now
     calls `graph.list_constraints()` first and only creates the constraint if an equivalent one
     (`type=UNIQUE`, `label=Entity`, `node_id` in `properties`) isn't already present.
  3. **`Graph.list_constraints()` itself fails on a brand-new, never-written-to graph**
     (`Invalid graph operation on empty key`) — so the idempotency check above has to treat that
     specific failure as "no constraints exist yet" (empty list), not propagate it as a real error.
  Also confirmed empirically against a live container: `SET n += $props` (map-merge) and dynamic
  node labels via a literal-interpolated `SET n:<Label>` both work exactly as designed in v6;
  `list_constraints()`'s `status` field returns `"OPERATIONAL"` (not `"ACTIVE"`) once a constraint
  is live — relevant for `db/health.py`'s constraint-status check, still to be implemented.
- **v6 — drop `LOAD CSV`/file-staging entirely; loader is pure Python + parameterized
  Cypher.** Discovered while implementing `db/driver.py` against the real `falkordb-py` client
  (v≈1.0, installed via `uv sync`): `Graph` exposes native `create_node_unique_constraint`,
  `create_node_range_index`, `list_constraints`, `query`/`ro_query`, `delete` — no need to hand-write
  `GRAPH.CONSTRAINT CREATE` text. Given that, and since schema validation (`db/schema.py`, v5)
  already requires reading `kg_nodes.csv`/`kg_edges.csv` into Python before any write, node
  loading is also done as a Python-generated `UNWIND $rows AS r MERGE ...` batch query — the same
  pattern v5 already used for edges/labels — instead of `LOAD CSV` from a file. This removes:
  - `db/prepare.py`'s file-staging step (BOM handling is just `open(path, encoding="utf-8-sig")`
    in Python — no need to write a second BOM-free copy to disk for `LOAD CSV` to read).
  - `docker-compose.yml`'s `./data/import:/data/import:ro` volume mount (nothing reads from the
    container filesystem anymore).
  - `cypher/01_constraints.cypher` and `cypher/02_load_nodes.cypher` as static files — constraint
    creation is now direct `Graph.create_node_unique_constraint("Entity", "node_id")` calls in
    `db/loader.py`, and node loading is a Python-generated `UNWIND` batch, same as edges.
  - `cypher/` folder now contains only `05_verify.cypher` (the one step that's a genuine static
    aggregate query with no per-row dynamic-type concern) + `README.md` explaining why 01–04
    aren't there.
  This also simplifies `tests/integration/conftest.py` — no volume-mount coordination between the
  fixture CSVs and the testcontainers-managed FalkorDB instance is needed; fixture rows are read
  in Python and passed as query parameters, identically to how the real loader will run.
- **v5 — graph database pivot: Neo4j → FalkorDB.** Reason: the user wants the
  KG to grow into GraphRAG/AI-assisted retrieval (Phase 3+), and FalkorDB keeps the graph and
  vector search in one engine (native `CREATE VECTOR INDEX` + `db.idx.vector.queryNodes`),
  avoiding a second retrieval store later. This version **replaces every Neo4j/APOC-specific
  design decision** from v1–v4; sections A and D are rewritten below. Sections B (observability),
  most of E (test policy/markers/CI-gate philosophy), and F's Makefile/CI shape are DB-agnostic
  and carry over with only naming changes (`kgme_falkordb` vs `kgme_neo4j`, etc.).

  **Researched tradeoffs that informed this pivot** (see chat for full sourcing):
  - Licensing: FalkorDB is **source-available**, not OSI open source (Neo4j Community is GPLv3).
    Worth a licensing/procurement check given the GxP/pharma context, but not a technical blocker.
  - No APOC. FalkorDB implements openCypher `MERGE` natively (nodes AND relationships), so
    idempotent loading is achievable — but the *specific* mechanism CLAUDE.md hard-codes
    (`apoc.merge.relationship` with a **dynamic relationship type per CSV row**) has no direct
    equivalent, because plain Cypher (on both Neo4j and FalkorDB) cannot parameterize a
    relationship type. Resolved below by grouping edges by `relation` and issuing one
    allow-listed, literal-typed `MERGE` per relation group (§D).
  - Dynamic node labels: same issue for `node_type` → label promotion (was
    `apoc.create.addLabels`); resolved the same way (group by `node_type`, one literal-labeled
    `SET` per group), validated against `kg_data_dictionary.csv`'s enum.
  - **Read-only enforcement gets *stronger*, not weaker.** Neo4j Community has no real RBAC (the
    original plan flagged this as a known limitation, enforced only at the app layer). FalkorDB
    sits on Redis, which has genuine **ACL users** — `ACL SETUSER kgme_reader on '>pw' '~*' '+@read' '+GRAPH.RO_QUERY'`
    — combined with FalkorDB's own `GRAPH.RO_QUERY` command, which the *engine* rejects write
    clauses on. This gives Phase 3's `cypher_guard` a real DB-level backstop, not just a
    static-analysis regex — directly serves CLAUDE.md's "agent DB access is read-only" rule
    better than the Neo4j Community plan could.
  - Test isolation gets simpler: FalkorDB graphs are named and cheap to create/drop
    (`GRAPH.DELETE`), so each integration test can use its own graph name instead of
    wipe-and-reuse against one shared database (which was the Neo4j Community workaround since
    Community can't do `CREATE DATABASE`).
  - No dedicated `testcontainers` module for FalkorDB — use `testcontainers`' generic
    `DockerContainer` API against the official `falkordb/falkordb` image instead of
    `testcontainers[neo4j]`.
  - `LOAD CSV` is supported natively since FalkorDB v4.6 — the existing node-loading pattern
    (`02_load_nodes`) carries over almost unchanged.

## Context

The repo (`kg-migration-engine/`) is at the boundary between Phase 0 (scaffolding — done) and
Phase 1 (graph ingestion — cypher scripts + Docker Compose exist for Neo4j, but no Python loader,
no tests beyond a version smoke test). Before writing Phase 1 code, the user wants the repo
brought to a production-grade baseline across three axes:

1. **Monitoring** — lightweight: structured `structlog` audit-event schema (the log *is* the GxP
   audit trail per `CLAUDE.md`), health/readiness checks, metrics as structured log events. No new
   infra yet; the event schema upgrades to a real metrics backend later without touching call
   sites. (Unchanged by the FalkorDB pivot — DB-agnostic.)
2. **Folder/package structure** — finalized before Phase 1 code lands. Unit vs. integration vs.
   contract tests, a home for cross-cutting observability/error code, CLI entry points.
3. **KG correctness (this plan's scope)** — the Phase 1 loader only, **now targeting FalkorDB**:
   `db/driver.py`, `db/schema.py` (new — validates CSV enums against `kg_data_dictionary.csv`
   before load), `db/prepare.py`, `db/loader.py`, plus fixture-based integration tests
   (idempotency remains the single most important test). Disposition parsing, agents,
   `cypher_guard`, API, dashboard (Phases 2–4) stay out of scope, but `driver.py`'s read-only path
   is deliberately the seam `agents/cypher_guard.py` builds on later — now backed by a real
   FalkorDB/Redis ACL read-only user, not just an app-level static validator.

Non-negotiables from `CLAUDE.md` that still apply, DB-agnostic: provenance
(`confidence`/`source_doc`/`source_ref`) mandatory on every node/edge; never graph-load
`kg_process_master.csv`; read CSVs as `utf-8-sig`; fail fast and loud on data/provenance
violations, never fabricate. **`CLAUDE.md`'s Neo4j/APOC-specific hard rules must be rewritten**
(see §D and the "CLAUDE.md updates" note at the end) — this is a required part of this plan, not
an afterthought, since those rules are what an agent reads every session.

---

## A) Final folder structure

```
kg-migration-engine/
├── src/kgme/
│   ├── __init__.py                     # unchanged: __version__
│   ├── config.py                       # CHANGED: falkordb_host/port/username/password/graph
│   │                                    # replace neo4j_uri/user/password/database
│   ├── logging.py                      # unchanged: re-export shim -> core.observability.get_logger
│   ├── core/
│   │   ├── __init__.py
│   │   ├── observability.py            # unchanged from v1-4 (DB-agnostic)
│   │   └── exceptions.py               # unchanged shape; add SchemaViolationError (see D)
│   ├── db/
│   │   ├── __init__.py
│   │   ├── driver.py                   # REWRITTEN: FalkorDB client, ro_query vs query (no sessions)
│   │   ├── schema.py                   # NEW: loads kg_data_dictionary.csv, validates relation/
│   │   │                                # node_type enums before load (needed because dynamic
│   │   │                                # types must be allow-listed literals — see D)
│   │   ├── loader.py                   # v6: reads kg_nodes/kg_edges.csv (utf-8-sig) directly in
│   │   │                                # Python; constraints via Graph.create_node_unique_constraint;
│   │   │                                # nodes/edges/labels all loaded as UNWIND-batched MERGE —
│   │   │                                # no prepare.py, no LOAD CSV, no container file-mounting
│   │   └── health.py                   # REWRITTEN: drop check_apoc_available; add check_constraints
│   │                                    # against FalkorDB's constraint listing
│   ├── cli.py                          # unchanged shape: `kgme-load`, `kgme db check`
│   ├── enrichment/__init__.py          # placeholder for Phase 2
│   ├── agents/__init__.py              # placeholder for Phase 3 (will use vector index + ro_query)
│   ├── api/__init__.py                 # placeholder for Phase 4
│   └── dashboard/__init__.py           # placeholder for Phase 4
├── tests/                              # SAME layout as v1-4 (unit/integration/contract/fixtures)
│   ├── __init__.py
│   ├── unit/{__init__.py, test_smoke.py, test_config.py, test_observability.py, test_schema.py}
│   ├── integration/{__init__.py, conftest.py, test_loader.py, test_health.py}
│   ├── contract/{__init__.py, test_data_dictionary_contract.py}
│   └── fixtures/{kg_nodes_fixture.csv, kg_edges_fixture.csv}
├── cypher/
│   ├── 05_verify.cypher                # the one genuinely static step: count/provenance assertions
│   └── README.md                       # NEW (v6): notes that 01-04 are NOT static files — constraints
│                                        # are direct Graph.create_node_unique_constraint(...) calls,
│                                        # and nodes/edges/labels are Python-generated UNWIND batches
│                                        # in db/loader.py (dynamic-type limitation, see plan §D) —
│                                        # points here so nobody goes looking for missing 01-04 files
├── docs/
│   ├── plans/phase1-restructuring.md   # this file — versioned plan of record (v5 = FalkorDB pivot)
│   ├── IMPLEMENTATION_PLAN.md          # UPDATE NEEDED: its Neo4j-specific cypher snippets (§1.2)
│   │                                    # are now historical/superseded — annotate, don't silently
│   │                                    # diverge from the north-star doc
│   ├── HANDOFF_DETAIL.md               # unchanged (data methodology, DB-agnostic)
│   ├── DATA_MODEL.md                   # NEW stub
│   ├── AUDIT.md                        # NEW stub
│   ├── OBSERVABILITY.md                # NEW
│   └── TESTING.md                      # NEW
├── docker-compose.yml                  # REWRITTEN: falkordb/falkordb image (server+browser), no APOC
├── .env.example                        # REWRITTEN: FALKORDB_* vars replace NEO4J_* vars
├── pyproject.toml                      # deps: falkordb client replaces neo4j driver; testcontainers
│                                        # (generic) replaces testcontainers[neo4j]
├── Makefile                            # target names unchanged; `make load` still `uv run kgme-load`
└── .github/workflows/ci.yml            # unchanged shape (unit/integration/contract split)
```

`scripts/load.sh` / `scripts/prepare_data.sh` (shell fallback) get rewritten to call
`redis-cli`/`GRAPH.QUERY` via the FalkorDB CLI instead of `cypher-shell`, or — simpler and
recommended — **retired in favor of `make load-legacy` = `uv run kgme-load` too**, since there's
no longer a meaningfully different "shell path" once cypher-shell is gone; keeping two Python
entry points pointing at the same code is simpler than maintaining a second shell implementation
of the now-more-complex (grouped-query) edge/label loading logic.

---

## B) Structured logging / audit-event schema — unchanged from v1–v4

`src/kgme/core/observability.py`'s event envelope (`event`, `component`, `status`, `run_id`,
`duration_ms`, `count`, `error_type`/`error_detail`, `node_id`/`edge_id`/`confidence`/`source_doc`)
is entirely DB-agnostic and carries over as designed in v1. `component` values change from
`"db.loader"` etc. (already generic) — no rename needed.

---

## C) Health / readiness checks (`src/kgme/db/health.py`) — revised

```python
def check_connectivity(client, *, graph_name: str) -> CheckResult: ...
    # liveness: client.connection.ping() — Redis-level ping, cheaper than any graph query

def check_graph_selected(client, *, graph_name: str) -> CheckResult: ...
    # readiness: select_graph(graph_name) succeeds and responds to a trivial `RETURN 1`

def check_constraints(client, *, graph_name: str) -> CheckResult: ...
    # readiness: the node_id uniqueness constraint exists and is ACTIVE (not PENDING/FAILED) —
    # FalkorDB constraints are created asynchronously (GRAPH.CONSTRAINT CREATE returns PENDING
    # immediately); this check's implementation must poll/verify ACTIVE status via FalkorDB's
    # constraint-listing mechanism — confirm exact command at implementation time (research
    # flagged this as needing verification against current FalkorDB docs, not assumed)

def check_provenance_complete(client, *, graph_name: str) -> CheckResult: ...
    # deep: executes cypher/05_verify.cypher's assertions, same design as v1

def run_health_checks(client, *, graph_name: str, deep: bool = False) -> HealthReport: ...
    # deep=False: connectivity + graph_selected + constraints (cheap)
    # deep=True: also check_provenance_complete
```

`check_apoc_available` is **removed** (no APOC in FalkorDB — nothing to check). No new check
replaces it; the underlying capability it protected (idempotent relationship merge) is now
guaranteed by construction in `loader.py`'s design (§D), not by a runtime capability probe.

---

## D) `driver.py`, `schema.py`, and `loader.py` — the FalkorDB-specific rewrite

### `src/kgme/db/driver.py`

FalkorDB's Python client (`falkordb` package) is stateless per call against a selected named
graph — no session/context-manager model like the `neo4j` driver. This actually simplifies the
chokepoint:

```python
class AccessMode(str, Enum):
    READ = "READ"; WRITE = "WRITE"

def build_client(settings: Settings) -> FalkorDB:
    """FalkorDB(host=settings.falkordb_host, port=settings.falkordb_port,
    username=settings.falkordb_username, password=settings.falkordb_password).
    Verifies connectivity eagerly (ping); raises ConnectionUnavailableError with
    a clear message if unreachable — fail fast, per CLAUDE.md."""

def get_graph(client: FalkorDB, *, settings: Settings) -> Graph:
    """client.select_graph(settings.falkordb_graph) — the single chokepoint for
    obtaining a Graph handle. loader.py and health.py both go through this."""

def run_query(graph: Graph, cypher: str, params: dict | None = None, *, mode: AccessMode = AccessMode.WRITE) -> Any:
    """mode=WRITE -> graph.query(cypher, params); mode=READ -> graph.ro_query(cypher, params).
    GRAPH.RO_QUERY is enforced by the FalkorDB engine itself (rejects write clauses),
    which is a real DB-level guarantee — this is the function Phase 3's cypher_guard
    and agents/as_is.py will call with mode=READ, backed by a scoped Redis ACL user
    (see the CLAUDE.md update note below) for defense in depth."""

def read_only_query(graph: Graph, cypher: str, params: dict | None = None) -> Any:
    """run_query(..., mode=AccessMode.READ) under a discoverable name, so a future
    agent import can't accidentally default to WRITE — same rationale as v1."""
```

No `close_driver` equivalent needed in the same way (client owns a connection pool); expose
`close_client(client)` for symmetry/test teardown regardless.

### `src/kgme/db/schema.py` (new)

Needed because relationship types and node-label promotion must be **literal, allow-listed
strings** in the generated Cypher (§ below) — plain Cypher (FalkorDB or Neo4j) cannot parameterize
a relationship type or label. This module is the single place that enum is loaded and validated:

```python
@dataclass(frozen=True)
class DataDictionary:
    allowed_node_types: frozenset[str]
    allowed_relations: frozenset[str]

def load_data_dictionary(path: Path) -> DataDictionary:
    """Parses data/raw/kg_data_dictionary.csv (utf-8-sig) for the node_type and
    relation enums."""

def validate_rows_against_dictionary(
    nodes: Sequence[Mapping[str, str]],
    edges: Sequence[Mapping[str, str]],
    dictionary: DataDictionary,
) -> None:
    """Raises SchemaViolationError (new in core/exceptions.py) listing every
    row whose node_type/relation is NOT in the dictionary's enum — fail fast,
    per CLAUDE.md, rather than silently interpolating an unvalidated string into
    a Cypher label/relationship-type position (the injection-safety reason this
    module exists at all). Called by loader.py before any write."""
```

### `src/kgme/db/loader.py` — steps 03/04 become Python-generated, grouped queries

```python
NON_ABORTING_STEPS: Final[frozenset[str]] = frozenset({"05_verify"})
# unchanged philosophy from v2: every step aborts the run on failure except 05_verify,
# which never raises and instead returns StepResult.ok = AND of its boolean columns.

def run_constraints_step(graph, logger) -> StepResult: ...
    # executes cypher/01_constraints.cypher's statements (index + GRAPH.CONSTRAINT CREATE)

def run_load_nodes_step(graph, logger) -> StepResult: ...
    # executes cypher/02_load_nodes.cypher (LOAD CSV + MERGE) — unchanged pattern

def run_load_edges_step(graph, edges: Sequence[Mapping[str, str]], dictionary: DataDictionary, logger) -> StepResult:
    """Groups `edges` (already validated against `dictionary` by schema.py) by
    `relation`. For each distinct relation value (a literal from the allow-list,
    never raw CSV text spliced without validation):

        UNWIND $rows AS r
        MATCH (s:Entity {node_id: r.source_id})
        MATCH (t:Entity {node_id: r.target_id})
        MERGE (s)-[rel:<RELATION_LITERAL> {edge_id: r.edge_id}]->(t)
        SET rel.confidence = r.confidence, rel.source_doc = r.source_doc,
            rel.source_ref = r.source_ref, rel.notes = r.notes

    executed once per relation group (batched via UNWIND, not row-by-row) — this
    both replaces apoc.merge.relationship's dynamic-type trick AND is idempotent
    (MERGE keyed on edge_id, same guarantee as v1-v4's apoc-based design)."""

def run_promote_labels_step(graph, nodes: Sequence[Mapping[str, str]], dictionary: DataDictionary, logger) -> StepResult:
    """Same grouping pattern over `node_type`: for each distinct node_type,
        MATCH (n:Entity {node_type: '<NODE_TYPE_LITERAL>'}) SET n:<NODE_TYPE_LITERAL>
    — replaces apoc.create.addLabels. Idempotent: adding an already-present label
    is a no-op in FalkorDB as in Neo4j."""

def run_verify_step(graph, logger) -> StepResult:
    """Executes cypher/05_verify.cypher. NON_ABORTING: parses the returned boolean
    columns into StepResult.summary; StepResult.ok = all(...). Never raises."""

def load_graph(client: FalkorDB, *, settings: Settings, wipe: bool = False) -> list[StepResult]:
    """1. bind run_id, log 'loader.run.started'
       2. prepare_import_files(...) — utf-8-sig staging (unchanged from v2)
       3. read kg_nodes.csv/kg_edges.csv rows in Python (needed anyway for the
          grouping in step 4-5); load_data_dictionary + validate_rows_against_dictionary
          — raises SchemaViolationError (aborts) on any row with an unlisted
          node_type/relation, BEFORE any write touches the graph
       4. graph = get_graph(client, settings=settings); if wipe: graph.delete()
          (FalkorDB's GRAPH.DELETE — simpler than Neo4j's DETACH DELETE-all, and
          still double-gated behind the same wipe=True + KGME_ALLOW_WIPE=1 pattern
          from v2, since it's equally destructive)
       5. run_constraints_step -> run_load_nodes_step -> run_load_edges_step ->
          run_promote_labels_step -> run_verify_step, in order; any raised
          exception except from run_verify_step aborts immediately (log
          'loader.run.aborted', re-raise as LoadAbortedError)
       6. log 'loader.run.completed', return list[StepResult]"""
```

### `cli.py` — unchanged exit-code logic from v2

`main_load()` still must catch `LoadAbortedError` (exception, not a `StepResult` row) **and**
check `any(not r.ok for r in results)` after a normal return (05_verify failing) — both paths
exit 1. `SchemaViolationError` (new) is also a subtype of `KgmeError` and is caught the same way
as `LoadAbortedError` — both are "the run never got to a clean StepResult list."

### `core/exceptions.py` addition

```python
class SchemaViolationError(KgmeError): ...   # new: raw CSV row uses an unlisted node_type/relation
```

### `docker-compose.yml` (rewritten)

```yaml
services:
  falkordb:
    image: falkordb/falkordb:latest     # bundles server (6379) + browser UI (3000)
    container_name: kgme-falkordb
    ports:
      - "6379:6379"
      - "3000:3000"   # FalkorDB Browser (replaces Neo4j Browser in README's Quickstart)
    environment:
      REDIS_ARGS: "--requirepass ${FALKORDB_PASSWORD:-changeme-in-.env} --appendonly yes"
    volumes:
      - falkordb_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "-a", "${FALKORDB_PASSWORD:-changeme-in-.env}", "ping"]
      interval: 10s
      timeout: 5s
      retries: 10
volumes:
  falkordb_data:
```
A commented-out block documents the optional read-only ACL user setup (`ACL SETUSER kgme_reader
on '>...' '~*' '+@read' '+GRAPH.RO_QUERY'`) for Phase 3, since it's not needed until the
NL→Cypher agent exists but the seam should be visible now.

### `.env.example` (rewritten)

```
FALKORDB_HOST=localhost
FALKORDB_PORT=6379
FALKORDB_USERNAME=
FALKORDB_PASSWORD=
FALKORDB_GRAPH=kgme
ANTHROPIC_API_KEY=
```

### `src/kgme/config.py` changes

Replace `neo4j_uri`/`neo4j_user`/`neo4j_password`/`neo4j_database` fields with
`falkordb_host` (default `"localhost"`), `falkordb_port` (default `6379`), `falkordb_username`
(default `None`), `falkordb_password` (`Field(...)`, required), `falkordb_graph` (default
`"kgme"`). `anthropic_api_key`/`anthropic_model` unchanged.

### `pyproject.toml` dependency changes

Remove `neo4j>=5.26`; add `falkordb` (pin exact current version at implementation time via PyPI —
Context7 quota was exhausted during research, verify before pinning). Remove
`testcontainers[neo4j]>=4.5`; add plain `testcontainers>=4.5` (generic `DockerContainer` API,
since no dedicated FalkorDB module exists).

### `CLAUDE.md` hard-rules rewrite (required — flagging explicitly, not deferred)

The following bullet in `CLAUDE.md`'s "Hard rules" section is Neo4j/APOC-specific and must be
replaced once this plan executes:

> Relationships load via `apoc.merge.relationship` keyed on `edge_id`. **Never
> `apoc.create.relationship`** (duplicates on re-run).

becomes:

> Relationships load via `MERGE` keyed on `edge_id`, grouped by relation type (relationship types
> are literal Cypher, not parameterizable — see `db/schema.py`/`db/loader.py`). Every relation
> type and node type used in a write must be validated against `kg_data_dictionary.csv`'s enum
> first — **never interpolate an unvalidated string into a label or relationship-type position.**

and the tech stack line changes from "Neo4j + Python" to "FalkorDB + Python", and:

> Agent DB access is **read-only**. NL→Cypher must pass `cypher_guard`; a write-capable query in
> an agent path is a defect, not a nit.

gets a clause added: "...enforced in two layers: a scoped Redis ACL user (`+@read`,
`+GRAPH.RO_QUERY` only) at the DB layer, plus `cypher_guard`'s static validator at the app layer."

---

## E) Test strategy — mostly unchanged from v1–v4, FalkorDB-specific deltas only

**Fixture CSVs**: identical content/shape to v1 (6 nodes / 4 edges, documented/peripheral/
inferred/gap coverage, one `SUSPECTED_SOURCE` edge mirroring the flagship finding).

**`tests/integration/conftest.py`** (revised): generic `testcontainers.core.container.DockerContainer("falkordb/falkordb:latest")` with exposed port 6379, `REDIS_ARGS` env matching
docker-compose.yml. **Test isolation is simpler than v1's Neo4j-Community workaround**: instead of
`wipe_database()` between tests, each test that needs a clean graph selects its own uniquely-named
graph (`client.select_graph(f"test_{token}")`) and drops it (`graph.delete()`) at teardown — no
shared-state risk between tests, no wipe-confirmation dance needed for test isolation specifically
(the guarded `wipe_database`/`--wipe` CLI flag still exists for its original dev-convenience
purpose, just isn't what tests use for isolation anymore).

**`tests/integration/test_loader.py`** — same 8 cases as v1/v2 (counts, idempotency [most
important], node/edge provenance, no dangling endpoints, uniqueness constraint enforced, labels
promoted, critical-step-failure aborts, wipe requires confirmation) — **plus one new case**:
`test_unlisted_relation_or_node_type_raises_schema_violation` — feed a fixture row with a
relation/node_type not in `kg_data_dictionary.csv` and assert `SchemaViolationError` is raised
before any write occurs (graph remains empty).

**`tests/unit/test_schema.py`** (new): `load_data_dictionary` parses the real
`kg_data_dictionary.csv` correctly (contract-adjacent, but this specific parsing unit is fast/pure
so lives in `unit/`, distinct from `tests/contract/`'s broader real-data assertions).

Everything else in §E (markers, `docs/TESTING.md` policy, coverage gate at 70%) is unchanged from
v1.

---

## F) `pyproject.toml` / `Makefile` / CI — unchanged shape, renamed deps only

Same `[project.scripts]`, `pytest` markers, coverage config, `Makefile` targets, and 3-job CI
split (`quality-and-unit` → `integration` → `contract`) as v1's §F. The only deltas: dependency
names (`falkordb` not `neo4j`, plain `testcontainers` not `testcontainers[neo4j]`), and
`make load-legacy` now also points at `uv run kgme-load` (see §A note on retiring the separate
shell path) rather than a maintained second shell implementation.

---

## Sequencing

1. `core/` (`observability.py`, `exceptions.py` incl. new `SchemaViolationError`); `logging.py` shim.
2. Empty `db/`, `enrichment/`, `agents/`, `api/`, `dashboard/` packages.
3. Move `tests/test_smoke.py` → `tests/unit/`; add `unit/integration/contract/fixtures` dirs.
4. Rewrite `docker-compose.yml`, `.env.example`, `config.py`, `pyproject.toml` deps for FalkorDB;
   update `CLAUDE.md`'s hard rules and tech-stack line (§D's rewrite, verbatim).
5. Update `pyproject.toml` markers/coverage/`Makefile`/CI; get the skeleton green with just the
   moved smoke test before any DB code.
6. `db/schema.py` + `tests/unit/test_schema.py` (parses the real data dictionary — fast, no Docker).
7. `db/driver.py` + unit tests (mockable FalkorDB client, no Docker).
8. `tests/integration/conftest.py` (generic DockerContainer against `falkordb/falkordb`) + fixture CSVs.
9. `cypher/01_constraints.cypher`, `02_load_nodes.cypher`, `05_verify.cypher` (rewritten for
   FalkorDB syntax where needed — verify `GRAPH.CONSTRAINT CREATE` syntax against current docs at
   implementation time); `cypher/README.md` explaining 03/04's absence.
10. `db/prepare.py` + `db/loader.py` (grouped-query 03/04 generation) +
    `tests/integration/test_loader.py` (idempotency test first, schema-violation test included).
11. `db/health.py` + `test_health.py`.
12. `cli.py`, console scripts, `Makefile`'s `load`/`db-check`, README quickstart update
    (FalkorDB Browser at :3000 replaces Neo4j Browser instructions).
13. `tests/contract/test_data_dictionary_contract.py` last.

## Verification

- `make test-unit` green, zero Docker dependency.
- `make up && make test-integration` green: fixture loads to exactly 6 nodes / 4 relationships;
  idempotency test passes on a second run; provenance/referential-integrity/constraint/label tests
  pass; the new schema-violation test correctly rejects an unlisted relation/node_type before any
  write.
- `make load` (`uv run kgme-load`) against the real `data/raw` CSVs yields
  `nodes_ok=true edges_ok=true provenance_ok=true` (55/47) — same outcome as the Neo4j-based
  design, different engine underneath.
- `uv run kgme db check --deep` reports `healthy=True` after a real load.
- FalkorDB Browser at `http://localhost:3000` renders the loaded graph (replaces the Neo4j Browser
  verification step from v1).
- `make lint` (ruff + mypy strict) clean on all new/changed modules.
- CI green across the three split jobs.
- `CLAUDE.md`'s hard rules read correctly for FalkorDB (no dangling references to `apoc.*` or
  "Neo4j" as the stack — a manual read-through, since this is a doc an agent trusts every session).
