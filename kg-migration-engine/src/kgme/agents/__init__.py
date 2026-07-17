"""Phase 3 (COMPLETE): As-Is / Migration-Mapping / GxP-Compliance agents, orchestrated via
LangGraph, with read-only DB access enforced by cypher_guard.

- cypher_guard.py, llm_client.py, schema_context.py, as_is.py: implemented (slice 1).
- mapping.py, compliance.py: implemented (slice 2).
- graph.py (LangGraph orchestration + routing), tests/eval/ (golden-question eval harness):
  implemented (slice 3).

Phase 4 (API + Dashboard) is the only remaining phase — see docs/IMPLEMENTATION_PLAN.md.
"""
