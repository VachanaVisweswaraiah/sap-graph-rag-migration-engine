<!--
NOTE: This file has been sanitized for public/private portfolio use.
Business logic, domain-specific rules, and proprietary details have been masked.
The coding patterns, architecture, and technical implementation remain authentic.
[MASKED] tags indicate where original business logic has been replaced.
-->

# Phase 4 — FastAPI backend + server-rendered dashboard (final phase)

## Context

Phase 3 is complete and merged (As-Is/Mapping/Compliance agents + LangGraph router + eval
harness). Phase 4 is the last phase in `docs/IMPLEMENTATION_PLAN.md` §4: a FastAPI backend
exposing the agent layer over HTTP, plus a two-view visualization dashboard. The user chose the
full scope (backend + dashboard) in one pass, and picked **FastAPI + server-rendered HTML/JS**
(Jinja2 templates, no separate JS build) over a React app — simplest to stand up, one deploy, no
Node toolchain, fits a two-view internal tool. Explicitly **no external CDN scripts** (no
Chart.js) — plain HTML tables + CSS-width bars only, so the dashboard works in an offline/
validated GxP environment with zero network dependency, consistent with `CLAUDE.md`'s existing
"don't fetch external sources automatically" discipline.

**Verified against the real, installed environment and real graph before designing anything:**
- `fastapi>=0.111` (resolved `0.139.0`) and `uvicorn>=0.30` are **already** declared dependencies
  — unused until now. `pydantic>=2.7`/`pydantic-settings>=2.3` also already present. `jinja2` is
  **not yet** a dependency — needs adding for `Jinja2Templates`.
- `src/kgme/api/__init__.py` and `src/kgme/dashboard/__init__.py` already exist as empty stub
  packages (created in Phase 1's scaffolding, never filled in).
- Confirmed the real `FastAPI.__init__` accepts `lifespan`; `TestClient` (from
  `fastapi.testclient`, wraps `starlette.testclient`) works against a real `FastAPI()` instance —
  this is the test pattern for the new integration tests, no assumptions from training data.
- **Real counts queried live against the loaded graph** (55 nodes, 50 edges — 3 more than
  slice 2's 47 baseline, from Phase 2's disposition `MIGRATES_TO` edges):
  - `confidence='gap'`: **3 nodes**, all in the `governance` module (`SOP`/`ReferencedDocument`
    nodes — `SOP:WI-0001`, `DOC:GXP_CRITICAL_TX`, `DOC:MODULE_RISK_ASSESSMENT`). The plan's
    original "3 gap nodes" figure still matches exactly.
  - `confidence='inferred'` edges: **7**, not the plan's original "4" — the 3 additional
    `MIGRATES_TO` edges from Phase 2's disposition parser weren't in the graph when that number
    was written. **This will be documented as a deviation in `docs/AUDIT.md`, not silently
    corrected without a note** — the real number is what `/gaps` reports.
  - Modules present: `MM` (24 nodes), `AM` (20), `cross` (8), `governance` (3) — no `value_stream`
    property exists anywhere (confirmed again), same substitution already made three times this
    session (schema_context.py, mapping.py, agents/graph.py's classifier). The
    `/value-stream/{id}/impact` route from the plan is renamed to **`/module/{module}/impact`**
    for honesty about what the graph actually contains — documented explicitly, not silently
    guessed.

## Design

### `src/kgme/db/gaps.py` (new) — fixed, no-LLM gap/inferred-edge listing

```python
@dataclass(frozen=True)
class GapNode:
    node_id: str
    node_type: str | None
    module: str | None
    source_doc: str | None

@dataclass(frozen=True)
class InferredEdge:
    source_id: str
    relation: str
    target_id: str
    source_doc: str | None

def list_gap_nodes(graph: Graph) -> list[GapNode]:
    """MATCH (n) WHERE n.confidence = 'gap' RETURN ... — fixed Cypher, no LLM,
    same trust model as agents/mapping.py and agents/compliance.py."""

def list_inferred_edges(graph: Graph) -> list[InferredEdge]:
    """MATCH (s)-[r]->(t) WHERE r.confidence = 'inferred' RETURN ..."""
```

### `src/kgme/api/service.py` (new) — shared aggregation, used by both API and dashboard

```python
@dataclass(frozen=True)
class ModuleImpact:
    module: str
    total_nodes: int
    gap_nodes: int
    peripheral_nodes: int
    documented_nodes: int
    mapping_coverage: ModuleCoverage  # reused from agents.mapping, not reimplemented

def compute_module_impact(graph: Graph, module: str) -> ModuleImpact:
    """Combines a small fixed node-count-by-confidence query with
    agents.mapping.compute_mapping_coverage(graph, module=module) — reuses that
    function verbatim rather than re-deriving MIGRATES_TO stats."""
```
This is the single source of truth both `GET /module/{module}/impact` (JSON) and the
`/dashboard/module-impact` HTML view call — no duplicated logic between API and dashboard.

### `src/kgme/api/schemas.py` (new) — Pydantic request/response models

`AskRequest`/`AskResponse` (mirrors `OrchestrationState`'s `route`/`final_answer`/`blocked`),
`NodeFactOut`/`EdgeFactOut` (every fact includes `confidence` + `source_doc`, per
`docs/IMPLEMENTATION_PLAN.md` §4.1's explicit requirement), `GapsResponse`,
`ModuleImpactResponse`, `HealthCheckOut`/`HealthResponse` (mirrors `db.health.CheckResult`/
`HealthReport`).

### `src/kgme/api/main.py` (new) — app factory + routes

```python
def create_app(
    *, client: FalkorDB, graph: Graph, anthropic_client: anthropic.Anthropic, settings: Settings
) -> FastAPI:
    """Builds the orchestration graph once (agents.graph.build_orchestration_graph +
    agents.schema_context.build_schema_context) and stores it + graph/client/settings
    on app.state. Explicit dependency injection (not global state), same DI style
    already used throughout agents/ and cli.py — this is what makes it testable:
    tests build their own app via create_app() with a real seeded test graph and a
    mocked Anthropic client (same pattern as tests/integration/test_as_is.py)."""
    app = FastAPI(title="kgme API")
    app.state.graph = graph
    app.state.orchestration_app = build_orchestration_graph(...)
    app.include_router(dashboard_router)
    # POST /ask, GET /gaps, GET /module/{module}/impact, GET /health
    return app

# Module-level app for `uvicorn kgme.api.main:app`, built from real settings.
app = create_app(
    client=build_client(_settings), graph=..., anthropic_client=..., settings=_settings
)
```
- `POST /ask` — `route_question(request.app.state.orchestration_app, body.question)`. Reuses
  slice 3's router verbatim.
- `GET /gaps` — `list_gap_nodes` + `list_inferred_edges` from `db/gaps.py`.
- `GET /module/{module}/impact` — `compute_module_impact` from `api/service.py`. 404 if the
  module isn't one of `agents.mapping.MODULES`.
- `GET /health` — reuses `db.health.run_health_checks(client, settings=settings)` verbatim
  (already returns exactly the shape needed).

### `src/kgme/dashboard/routes.py` + `src/kgme/dashboard/templates/*.html` (new)

Two views, both plain server-rendered HTML tables (no JS charting library, no CDN):
- **`/dashboard/module-impact`**: one row per module (`MM`/`AM`/`cross`/`governance`) — total
  nodes, gap/peripheral/documented counts, `MIGRATES_TO` coverage fraction. A CSS-width bar
  (`style="width: {pct}%"` on a `<div>`) gives the "size by doc depth"-style visual pivot the
  plan calls for, using real coverage-percentage data rather than a fabricated depth metric that
  doesn't exist in the graph.
- **`/dashboard/gaps`**: the 3 gap nodes + 7 inferred edges as first-class table rows (`node_id`/
  `module`/`source_doc` and `source`/`relation`/`target`/`source_doc` respectively). A static,
  hard-coded caption reading **"Centrality ranking: low signal until ATC scan + gap docs
  retrieved"** per the plan's own wording — no centrality is computed (none exists yet, and
  fabricating one would violate the project's "never guess, report honestly" discipline).

Both templates extend a minimal `base.html` (title, nav links between the two views, inline
`<style>` — no external assets of any kind).

### Dependencies

Add `jinja2` to `pyproject.toml` (`uv add jinja2`) — the only new dependency; fastapi/uvicorn/
pydantic are already present.

### `Makefile` addition

```
api:              ## run the FastAPI server (needs FalkorDB up + graph loaded)
	uv run uvicorn kgme.api.main:app --reload --port 8000
```

## Tests

- `tests/unit/test_gaps.py`: `list_gap_nodes`/`list_inferred_edges` row-shape mapping with a
  mocked graph (`ro_query.return_value.result_set = [...]`), mirroring `test_mapping.py`'s style.
- `tests/integration/test_gaps.py`: seed a gap node + an inferred edge via raw Cypher (same
  self-contained pattern as every other integration test this phase), assert both functions find
  them for real.
- `tests/unit/test_api_service.py`: `compute_module_impact` with a mocked graph.
- `tests/integration/test_api.py`: `TestClient(create_app(...))` against a real seeded FalkorDB
  test graph (`client`/`settings` fixtures) + a **mocked** Anthropic client (same
  `side_effect=[...]` pattern as `test_as_is.py`/`test_graph.py` — no real API calls in the
  default suite). One test per endpoint: `POST /ask`, `GET /gaps`, `GET /module/{module}/impact`
  (incl. a 404-for-unknown-module case), `GET /health`.
- `tests/integration/test_dashboard.py`: `TestClient` `GET` on both dashboard routes against the
  same seeded graph, asserting the rendered HTML contains the expected real values (module names,
  gap `node_id`s) — no snapshot testing, just targeted substring assertions.

## Sequencing

1. `uv add jinja2`; `db/gaps.py` (+ unit + integration tests).
2. `api/schemas.py`; `api/service.py` (`compute_module_impact`, reusing
   `agents.mapping.compute_mapping_coverage`) + unit tests.
3. `api/main.py` (`create_app` + the four routes) + `tests/integration/test_api.py`.
4. `dashboard/routes.py` + templates + `tests/integration/test_dashboard.py`.
5. `Makefile` `api` target.
6. Real live verification: `make up` (already running) → `uv run uvicorn kgme.api.main:app --port
   8000` in the background → real `curl` against all four API routes and both dashboard routes
   (one real `POST /ask` call hits the real Anthropic API) → inspect responses manually → stop
   the server.
7. Update `docs/AUDIT.md`: the real `/gaps` counts (3 gap nodes, 7 inferred edges — noting the
   4→7 deviation from the original plan figure), the `/module/{module}/impact` rename rationale,
   and the dashboard's real rendered output.
8. Update `docs/IMPLEMENTATION_PLAN.md`'s Phase 4 status if it tracks phase completion inline
   (check first — don't guess).

## Verification

- `uv run pytest tests/unit/test_gaps.py tests/unit/test_api_service.py tests/integration/test_gaps.py tests/integration/test_api.py tests/integration/test_dashboard.py -v` green.
- `make lint` (ruff + mypy strict) clean.
- Full suite still green: `make test` (unit+integration, coverage gate) + `tests/contract`.
- Real live `curl` verification against all endpoints and both dashboard pages while the server
  runs against the actual loaded graph (not mocked) — matches the DoD in
  `docs/IMPLEMENTATION_PLAN.md` §4: "`POST /ask` answers a NL question end-to-end with citations;
  both dashboard views render from live graph data; `/health` green."

## Git workflow (same as before, per `CONTRIBUTING.md`)

Fresh feature branch from `main` (previous PR is merged), commands given at the end once
implementation and verification are complete: squash-merge, delete branch, recreate fresh next
time.
