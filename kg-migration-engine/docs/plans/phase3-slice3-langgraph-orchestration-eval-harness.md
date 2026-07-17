<!--
NOTE: This file has been sanitized for public/private portfolio use.
Business logic, domain-specific rules, and proprietary details have been masked.
The coding patterns, architecture, and technical implementation remain authentic.
[MASKED] tags indicate where original business logic has been replaced.
-->

# Phase 3, slice 3 — LangGraph orchestration + eval harness (completes Phase 3)

## Context

Slices 1 (As-Is agent) and 2 (Mapping + Compliance agents) are implemented and merged. All three
agents exist as independent, directly-callable modules but nothing routes a single incoming
question to the right one — `docs/IMPLEMENTATION_PLAN.md` §3.6 calls for a LangGraph state
machine that classifies a question and dispatches to whichever agent answers it, and §3.7 calls
for a golden-question regression harness. This slice implements both, completing Phase 3.

**Verified against the real, installed environment before designing anything:**
- `langgraph==1.2.7` is already an installed dependency (`pyproject.toml` pins `langgraph>=0.2`).
  Inspected the real `StateGraph` class on this machine (not assumed from memory/training data):
  `StateGraph(state_schema)`, `.add_node(name, fn)`, `.add_conditional_edges(source, path_fn,
  path_map)`, `.compile(...) -> CompiledStateGraph`.
- Each existing agent has a different call shape — this drives the design:
  - `AsIsQueryAgent.ask(question: str) -> AsIsAnswer` (`agents/as_is.py`) — arbitrary NL question,
    LLM-generated Cypher, already produces a citation-carrying NL answer.
  - `build_mapping_report(graph, *, module, narrate, client, model) -> MappingReport`
    (`agents/mapping.py`) — fixed Cypher, optional `module` scope, optional NL narration layer.
  - `build_compliance_report(graph, *, narrate, client, model) -> ComplianceReport`
    (`agents/compliance.py`) — fixed Cypher, no question-derived parameters, optional narration.
- Existing test pattern for LLM-touching integration tests (`tests/integration/test_as_is.py`):
  real FalkorDB via the `client`/`settings` fixtures, **mocked** Anthropic client
  (`fake_client.messages.create.side_effect = [...]`) — no real API calls in the default test
  suite (cost + non-determinism). The eval harness intentionally breaks this pattern on purpose
  (see below) since its entire point is catching real model/prompt regressions.
- `pyproject.toml` already registers a `contract` pytest marker for the slow/data-focused suite
  that's excluded from `make test` and run as its own CI job (`.github/workflows/ci.yml`'s
  `contract` job). The eval harness follows the exact same isolation pattern with a new `eval`
  marker — real API calls are expensive/non-deterministic and must never run automatically in the
  default CI unit/integration jobs.

## Design

### `src/kgme/agents/graph.py` (new) — router + orchestration

```python
class OrchestrationState(TypedDict, total=False):
    question: str
    route: Literal["as_is", "mapping", "compliance"]
    module: str | None          # only set if the classifier confidently extracted one
    final_answer: str
    blocked: bool                # surfaced from as_is's cypher_guard rejection, if any

_CLASSIFY_TOOL = {...}  # tool-forced call, same pattern as as_is.py's _CYPHER_TOOL:
    # route: enum ["as_is", "mapping", "compliance"]
    # module: enum ["MM", "AM", "cross", "governance"] | null — only set when the question
    #         clearly names one; "fails closed" — never guess, leave null for a full report.

def classify_question(client, *, model, question) -> tuple[str, str | None]:
    """Tool-forced classification call. Pure function, independently unit-testable
    with a mocked client — no graph access, no side effects."""

def _as_is_node(client, graph, *, model, schema_context, logger) -> Callable[[state], state]:
    """Delegates to AsIsQueryAgent.ask(question) — reuses the existing agent verbatim,
    no new answer-composition logic. Sets final_answer + blocked."""

def _mapping_node(client, graph, *, model) -> Callable[[state], state]:
    """Calls build_mapping_report(graph, module=state.get('module'), narrate=True, ...).
    final_answer = report.narrative (guaranteed non-None since narrate=True)."""

def _compliance_node(client, graph, *, model) -> Callable[[state], state]:
    """Calls build_compliance_report(graph, narrate=True, ...). final_answer = report.narrative."""

def build_orchestration_graph(client, graph, *, model, schema_context, logger) -> CompiledStateGraph:
    """Wires classify -> {as_is, mapping, compliance} -> END via add_conditional_edges,
    keyed on state['route']. Node functions are built as closures over client/graph/model
    (same DI style already used by AsIsQueryAgent's constructor) so they stay unit-testable
    in isolation from the graph wiring itself."""

def route_question(app: CompiledStateGraph, question: str) -> str:
    """Convenience wrapper: app.invoke({'question': question}) -> final_answer string.
    This is what the CLI calls."""
```

Design decisions, stated explicitly so they're reviewable:
- **Classification only ever picks a route + optional module — never generates Cypher itself.**
  All three downstream agents keep their own independent, already-verified query logic
  (LLM-generated-and-guarded for as_is, fixed for mapping/compliance). The router adds a
  dispatch layer on top, it does not change any agent's internal trust model.
- **`module` extraction fails closed**: the classify tool's `module` field defaults to `null`
  unless the question clearly names one of the four real module values. An ambiguous question
  ("show me the migration gaps") runs the Mapping agent's full multi-module report rather than
  guessing a module — consistent with the disposition parser's established "never guess, log/
  report honestly instead" pattern from Phase 2.
- **Mapping/Compliance nodes always narrate** (`narrate=True`) when reached through the
  orchestrator, since the whole point of routing is to produce one coherent NL answer; the
  underlying structured `ModuleCoverage`/`ComplianceFinding` data is still there if a caller wants
  it (`route_question` returns text, but the node functions are unit-testable independently and
  can be called directly for the structured form, same as the CLI's existing `kgme map`/
  `kgme compliance-scan` commands already do).
- **`kgme ask` is untouched.** A new `kgme route "<question>"` CLI subcommand is added instead,
  so the existing direct As-Is-only command has zero behavior change — no regression risk to
  slice 1's already-verified command.

### `src/kgme/cli.py` addition

```
kgme route "<question>"
```
Builds the orchestration graph once per invocation (same construction cost as `kgme ask` today:
one `build_client`/`get_graph`/`build_anthropic_client`/`build_schema_context` call), invokes it,
prints the final answer. Exits 1 if the as_is branch was blocked by cypher_guard (mirrors `kgme
ask`'s existing exit-code contract).

### Tests

- `tests/unit/test_graph.py`: `classify_question` with a mocked client — asserts each of the
  three routes is selected for a representative question, asserts `module` stays `None` when the
  question doesn't name one and is correctly extracted when it does. Node-function unit tests with
  mocked client/graph, mirroring `tests/unit/test_mapping.py`'s/`test_compliance.py`'s existing
  mock style — verifying each node sets `final_answer` from the right underlying call.
- `tests/integration/test_graph.py`: real FalkorDB (seeded via raw Cypher, same self-contained
  pattern as `test_mapping.py`/`test_compliance.py`), **mocked** Anthropic client covering both the
  classify call and the downstream node's call(s) via `side_effect`. Three tests — one per route —
  invoking the fully compiled graph end-to-end and asserting the right node ran (e.g. mapping route
  actually queries `MIGRATES_TO` coverage from the seeded graph, not a stub).
- `tests/unit/test_cli.py`: `test_route_*` tests mirroring the existing `test_ask_*` tests exactly
  (mocked `build_orchestration_graph`/`route_question`).

### `tests/eval/` (new directory) — golden-question eval harness

- New pytest marker `eval` registered in `pyproject.toml` (alongside the existing `integration`/
  `contract` markers), **excluded from `make test`** (matches how `contract` is already isolated).
  New `Makefile` target `make eval` (`uv run pytest tests/eval -m eval`) — manual-only, requires
  a real `ANTHROPIC_API_KEY` and a loaded graph, same operating discipline already used for every
  `--narrate` real-API run this session (never automated in CI, always manually inspected).
- `tests/eval/test_agents_eval.py`: a **golden-question set** of 12 real Q&A pairs against the
  live-verified graph facts already confirmed this session and in slice 1 — e.g.:
  - as_is: "What SAP transactions does business process PROC01 use?" → must mention `ZX_SAMPLE_01`
    and `[documented]` (matches slice 1's real verified answer).
  - mapping: "What's the MIGRATES_TO coverage for MM?" → must state `3` and `12` (or "3/12").
  - mapping: "What's mapped in AM?" → must state `0` and not imply coverage exists (regression
    guard against the exact failure mode flagged in `docs/AUDIT.md`: silently glossing over a
    real gap).
  - compliance: "What are the GxP compliance risks?" → must mention `QM:BATCH_RELEASE` and
    `SYS:LAB_SYSTEM` first/prominently (the flagship finding).
  - A handful of routing-only assertions (question → expected route, no answer-content check)
    to catch classifier drift independent of downstream answer quality.
  Each assertion is a substring/contains-style check against the real model's real output (never
  exact-match — model phrasing varies), run against the real Anthropic API and the real graph.
  **This is a genuine regression net, run manually before/after prompt or model changes** — not
  part of the automated CI gate, per the marker isolation above.

### `.github/workflows/ci.yml`

No changes. The `eval` job is intentionally **not** added to CI (would need a committed API key
secret, has real dollar cost per run, and is non-deterministic by nature) — it stays a manual
`make eval` command, documented in `docs/AUDIT.md`'s verification trail the same way this
session's manual `--narrate` runs already are.

## Sequencing

1. `agents/graph.py`: `classify_question` + `_CLASSIFY_TOOL` + unit tests (mocked client, no
   graph) — smallest independently-testable piece first.
2. `agents/graph.py`: node functions (`_as_is_node`/`_mapping_node`/`_compliance_node`) +
   `build_orchestration_graph`/`route_question` + unit tests (mocked client/graph per node).
3. `tests/integration/test_graph.py`: three end-to-end routing tests against a real seeded graph,
   mocked LLM.
4. `cli.py`: `kgme route "<question>"` + unit tests mirroring `test_ask_*`.
5. `pyproject.toml` new `eval` marker + `Makefile` `make eval` target + `tests/eval/
   test_agents_eval.py`'s 12-question golden set.
6. Real live verification: `uv run kgme route "..."` against the actual graph + real API for at
   least one question per route (manually inspected, same discipline as every prior slice), then
   `make eval` once against the real graph/API to confirm the golden set passes for real (not just
   under mocks).
7. Update `docs/AUDIT.md` with the orchestration layer's real findings and the eval harness's
   first real pass/fail results.
8. Update `src/kgme/agents/__init__.py`'s docstring — this is the last piece of Phase 3, mark it
   complete there (LangGraph orchestration + eval harness both done; only Phase 4 remains overall).

## Verification

- `uv run pytest tests/unit/test_graph.py tests/integration/test_graph.py tests/unit/test_cli.py -v` green.
- `make lint` (ruff + mypy strict) clean.
- Full suite still green: `make test` (unit+integration, coverage gate) + `tests/contract`.
- `uv run kgme route "What SAP transactions does business process PROC01 use?"` and one mapping-
  and one compliance-shaped question against the real graph — manually inspected for correct
  routing and correct, honest answers (no fabrication, gaps stated plainly).
- `make eval` run once for real against the live graph/API — record the pass/fail outcome in
  `docs/AUDIT.md` (a golden question failing here is useful signal, not necessarily a blocker,
  since model phrasing genuinely varies — but it must be looked at, not ignored).

## Git workflow (same as before, per `CONTRIBUTING.md`)

Fresh feature branch from `main` (prior PR merged), commands given at the end once
implementation and verification are complete: squash-merge, delete branch, recreate fresh next
time.
