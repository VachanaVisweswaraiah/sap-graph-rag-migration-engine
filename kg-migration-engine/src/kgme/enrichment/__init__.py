"""Enrichment sources that layer additional facts onto the graph, always at
confidence='inferred' pending human review (CLAUDE.md non-negotiable #2).

- s4_simplification.py: property enrichment from the SAP Simplification Item Catalog.
- Phase 2's s4_disposition-text parser (MIGRATES_TO/DEPRECATED_BY edges) is not yet
  implemented — placeholder per docs/IMPLEMENTATION_PLAN.md.
"""
