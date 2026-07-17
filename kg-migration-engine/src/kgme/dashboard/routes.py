"""Five server-rendered dashboard views: Module Impact, Gap Explorer,
Cross-Module Impact, Ask, and Agents. Plain HTML/inline JS only — no external JS
charting library, no CDN — so this works in an offline/validated GxP
environment with zero network dependency.

Module Impact, Gap Explorer, and Cross-Module Impact reuse the exact same data
functions the JSON API uses (api.service.compute_module_impact, db.gaps.
list_gap_nodes/list_inferred_edges, agents.impact.compute_reconciliation_chains)
— no duplicated query logic between the API and the dashboard. Ask is a static
shell whose inline script calls the existing POST /ask JSON endpoint
client-side — no server-side Q&A logic here, that already lives in
api/app_factory.py. Agents is a static reference page (the 6-agent architecture
explained in plain English) — no live graph query, since the architecture
doesn't change per request.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from kgme.agents.impact import compute_reconciliation_chains
from kgme.agents.mapping import MODULES
from kgme.api.service import compute_module_impact
from kgme.db.gaps import list_gap_nodes, list_inferred_edges

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
_templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

router = APIRouter(prefix="/dashboard")


@router.get("/module-impact", response_class=HTMLResponse)
def module_impact_view(request: Request) -> HTMLResponse:
    graph = request.app.state.graph
    modules = []
    for module in MODULES:
        impact = compute_module_impact(graph, module)
        total = impact.mapping_coverage.total_transactions
        pct = (impact.mapping_coverage.mapped_transactions / total * 100) if total else 0
        modules.append(
            {
                "module": impact.module,
                "total_nodes": impact.total_nodes,
                "gap_nodes": impact.gap_nodes,
                "peripheral_nodes": impact.peripheral_nodes,
                "documented_nodes": impact.documented_nodes,
                "mapping_coverage": impact.mapping_coverage,
                "coverage_pct": round(pct, 1),
                "s4_flagged_nodes": impact.s4_flagged_nodes,
            }
        )
    return _templates.TemplateResponse(
        request, "module_impact.html", {"modules": modules, "active_tab": "module-impact"}
    )


@router.get("/gaps", response_class=HTMLResponse)
def gaps_view(request: Request) -> HTMLResponse:
    graph = request.app.state.graph
    gap_nodes = list_gap_nodes(graph)
    inferred_edges = list_inferred_edges(graph)
    return _templates.TemplateResponse(
        request,
        "gaps.html",
        {"gap_nodes": gap_nodes, "inferred_edges": inferred_edges, "active_tab": "gaps"},
    )


@router.get("/impact", response_class=HTMLResponse)
def impact_view(request: Request) -> HTMLResponse:
    graph = request.app.state.graph
    chains = compute_reconciliation_chains(graph)
    return _templates.TemplateResponse(
        request, "impact.html", {"chains": chains, "active_tab": "impact"}
    )


@router.get("/ask", response_class=HTMLResponse)
def ask_view(request: Request) -> HTMLResponse:
    return _templates.TemplateResponse(request, "ask.html", {"active_tab": "ask"})


@router.get("/agents", response_class=HTMLResponse)
def agents_view(request: Request) -> HTMLResponse:
    """Static reference page -- no live graph query, unlike the other views. The
    6-agent architecture doesn't change per request, so there's nothing to fetch."""
    return _templates.TemplateResponse(request, "agents.html", {"active_tab": "agents"})
