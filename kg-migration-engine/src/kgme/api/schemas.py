"""Pydantic request/response models for the FastAPI backend. Every fact-bearing
model includes `confidence` + `source_doc`, per docs/IMPLEMENTATION_PLAN.md §4.1's
explicit requirement.
"""

from __future__ import annotations

from pydantic import BaseModel


class AskRequest(BaseModel):
    question: str


class AskResponse(BaseModel):
    route: str
    answer: str
    blocked: bool


class GapNodeOut(BaseModel):
    node_id: str
    node_type: str | None
    module: str | None
    confidence: str = "gap"
    source_doc: str | None


class InferredEdgeOut(BaseModel):
    source_id: str
    relation: str
    target_id: str
    confidence: str = "inferred"
    source_doc: str | None


class GapsResponse(BaseModel):
    gap_nodes: list[GapNodeOut]
    inferred_edges: list[InferredEdgeOut]


class MappingCoverageOut(BaseModel):
    total_transactions: int
    mapped_transactions: int
    mapped_pairs: list[tuple[str, str]]


class ModuleImpactResponse(BaseModel):
    module: str
    total_nodes: int
    gap_nodes: int
    peripheral_nodes: int
    documented_nodes: int
    mapping_coverage: MappingCoverageOut
    s4_flagged_nodes: dict[str, int]


class ChainHopOut(BaseModel):
    node_id: str
    confidence: str | None
    module: str | None
    inbound_total: int
    inbound_documented: int
    weak_neighbors: int


class ReconciliationChainOut(BaseModel):
    start: ChainHopOut
    investment_relation: str
    investment_confidence: str | None
    investment_source_doc: str | None
    mid1: ChainHopOut
    intermediate_relation: str
    intermediate_confidence: str | None
    intermediate_source_doc: str | None
    mid2: ChainHopOut
    reconciliation_relation: str
    reconciliation_confidence: str | None
    reconciliation_source_doc: str | None
    weakest_link_confidence: str | None


class ImpactChainsResponse(BaseModel):
    chains: list[ReconciliationChainOut]


class HealthCheckOut(BaseModel):
    name: str
    ok: bool
    detail: str
    duration_ms: float


class HealthResponse(BaseModel):
    healthy: bool
    checks: list[HealthCheckOut]
