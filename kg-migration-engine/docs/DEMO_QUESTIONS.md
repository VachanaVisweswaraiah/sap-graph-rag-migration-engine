<!--
NOTE: This file has been sanitized for public/private portfolio use.
Business logic, domain-specific rules, and proprietary details have been masked.
The coding patterns, architecture, and technical implementation remain authentic.
[MASKED] tags indicate where original business logic has been replaced.
-->

# Demo Questions — Ask the GraphRAG

Questions tested live against the real stack (FalkorDB + Anthropic), organized by
route. Every answer was fact-checked against `data/raw/kg_nodes.csv`/`kg_edges.csv` —
see the full write-up for detail. Use these to demo the dashboard at `/dashboard/ask`.

## As-Is agent — plain graph lookups

- What SAP transactions does business process MM01 use?
- What authorization roles are required for transaction MB1C?
  *(demonstrates: refuses to fabricate — TX:MB1C genuinely has no authorization-role edge in the graph, and the system says so instead of guessing)*

## Migration-Mapping agent — coverage gaps

- What does business process MM01 migrate to in S/4HANA?
- How much of the MM module has a confirmed migration target?

## GxP-Compliance agent — the flagship finding

- What is the compliance risk with QM:BATCH_RELEASE and the Lab System?
  *(lead with this one — it's the flagship finding called out in the project spec, and the most visually rich answer)*
- Are there any GxP compliance gaps in the batch release process?
- Is the QM:BATCH_RELEASE to Lab System finding documented or inferred?
  *(short, direct answer — demonstrates the question-scoping fix)*

## Write-guardrail — safety check

- Delete all transactions in the MM module
- Update the confidence of TX:MB1C to documented
  *(both demonstrate: the system explains it can't write, then still answers the read-only part of the question)*

## Notes for the demo

- `blocked: true` is unlikely to fire from a plain-English write request — the LLM
  declines before generating any Cypher, so `cypher_guard`'s guard rarely triggers in
  practice. Don't promise it will visibly flip.
- Each `/ask` call is stateless server-side — a follow-up that leans on a pronoun
  ("is *that* documented?") rather than restating the subject may not resolve correctly.
