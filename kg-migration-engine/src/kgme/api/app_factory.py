"""FastAPI app factory (Phase 4 §4.1). Explicit dependency injection — same DI
style already used throughout agents/ and cli.py — is what makes this testable:
tests call create_app() directly with a real seeded test graph and a mocked
Anthropic client, mirroring tests/integration/test_as_is.py's pattern, without
ever importing api/main.py (which builds a REAL client/graph/Anthropic client at
import time for uvicorn's benefit — importing it in a test would try to reach a
real FalkorDB and a real Anthropic API).
"""

from __future__ import annotations

import anthropic
from falkordb import FalkorDB, Graph
from fastapi import FastAPI, HTTPException

from kgme.agents.graph import build_orchestration_graph, route_question
from kgme.agents.impact import ChainHop, compute_reconciliation_chains
from kgme.agents.mapping import MODULES
from kgme.agents.schema_context import build_schema_context
from kgme.api.schemas import (
    AskRequest,
    AskResponse,
    ChainHopOut,
    GapNodeOut,
    GapsResponse,
    HealthCheckOut,
    HealthResponse,
    ImpactChainsResponse,
    InferredEdgeOut,
    MappingCoverageOut,
    ModuleImpactResponse,
    ReconciliationChainOut,
)
from kgme.api.service import compute_module_impact
from kgme.config import Settings
from kgme.core.observability import get_logger
from kgme.dashboard.routes import router as dashboard_router
from kgme.db.gaps import list_gap_nodes, list_inferred_edges
from kgme.db.health import run_health_checks


def create_app(
    *, client: FalkorDB, graph: Graph, anthropic_client: anthropic.Anthropic, settings: Settings
) -> FastAPI:
    app = FastAPI(title="kgme API")
    app.state.client = client
    app.state.graph = graph
    app.state.settings = settings
    app.state.orchestration_app = build_orchestration_graph(
        anthropic_client,
        graph,
        model=settings.anthropic_model,
        schema_context=build_schema_context(graph),
        logger=get_logger("api"),
    )
    app.include_router(dashboard_router)

    @app.post("/ask", response_model=AskResponse)
    def ask(body: AskRequest) -> AskResponse:
        result = route_question(app.state.orchestration_app, body.question)
        return AskResponse(
            route=result["route"],
            answer=result["final_answer"],
            blocked=result.get("blocked", False),
        )

    @app.get("/gaps", response_model=GapsResponse)
    def gaps() -> GapsResponse:
        gap_nodes = list_gap_nodes(app.state.graph)
        inferred_edges = list_inferred_edges(app.state.graph)
        return GapsResponse(
            gap_nodes=[
                GapNodeOut(
                    node_id=n.node_id,
                    node_type=n.node_type,
                    module=n.module,
                    source_doc=n.source_doc,
                )
                for n in gap_nodes
            ],
            inferred_edges=[
                InferredEdgeOut(
                    source_id=e.source_id,
                    relation=e.relation,
                    target_id=e.target_id,
                    source_doc=e.source_doc,
                )
                for e in inferred_edges
            ],
        )

    @app.get("/module/{module}/impact", response_model=ModuleImpactResponse)
    def module_impact(module: str) -> ModuleImpactResponse:
        if module not in MODULES:
            raise HTTPException(status_code=404, detail=f"Unknown module: {module!r}")
        impact = compute_module_impact(app.state.graph, module)
        return ModuleImpactResponse(
            module=impact.module,
            total_nodes=impact.total_nodes,
            gap_nodes=impact.gap_nodes,
            peripheral_nodes=impact.peripheral_nodes,
            documented_nodes=impact.documented_nodes,
            mapping_coverage=MappingCoverageOut(
                total_transactions=impact.mapping_coverage.total_transactions,
                mapped_transactions=impact.mapping_coverage.mapped_transactions,
                mapped_pairs=impact.mapping_coverage.mapped_pairs,
            ),
            s4_flagged_nodes=impact.s4_flagged_nodes,
        )

    @app.get("/impact/chains", response_model=ImpactChainsResponse)
    def impact_chains() -> ImpactChainsResponse:
        chains = compute_reconciliation_chains(app.state.graph)

        def hop_out(hop: ChainHop) -> ChainHopOut:
            return ChainHopOut(
                node_id=hop.node_id,
                confidence=hop.confidence,
                module=hop.module,
                inbound_total=hop.inbound_total,
                inbound_documented=hop.inbound_documented,
                weak_neighbors=hop.weak_neighbors,
            )

        return ImpactChainsResponse(
            chains=[
                ReconciliationChainOut(
                    start=hop_out(c.start),
                    investment_relation=c.investment_relation,
                    investment_confidence=c.investment_confidence,
                    investment_source_doc=c.investment_source_doc,
                    mid1=hop_out(c.mid1),
                    intermediate_relation=c.intermediate_relation,
                    intermediate_confidence=c.intermediate_confidence,
                    intermediate_source_doc=c.intermediate_source_doc,
                    mid2=hop_out(c.mid2),
                    reconciliation_relation=c.reconciliation_relation,
                    reconciliation_confidence=c.reconciliation_confidence,
                    reconciliation_source_doc=c.reconciliation_source_doc,
                    weakest_link_confidence=c.weakest_link_confidence,
                )
                for c in chains
            ]
        )

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        report = run_health_checks(app.state.client, settings=app.state.settings)
        return HealthResponse(
            healthy=report.healthy,
            checks=[
                HealthCheckOut(name=c.name, ok=c.ok, detail=c.detail, duration_ms=c.duration_ms)
                for c in report.checks
            ],
        )

    return app
