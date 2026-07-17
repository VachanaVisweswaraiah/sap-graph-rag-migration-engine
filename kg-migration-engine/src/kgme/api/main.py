"""NOTE: This file has been sanitized for public/private portfolio use.
Business logic, domain-specific rules, and proprietary details have been masked.
The coding patterns, architecture, and technical implementation remain authentic.
[MASKED] tags indicate where original business logic has been replaced.

Uvicorn entry point: `uvicorn kgme.api.main:app`. Builds the REAL client/graph/
Anthropic client at import time — tests must never import this module directly;
they call api.app_factory.create_app() with fakes/mocks instead (see
app_factory.py's docstring).
"""

from __future__ import annotations

from fastapi import FastAPI

from kgme.agents.llm_client import build_anthropic_client
from kgme.api.app_factory import create_app
from kgme.config import load_settings
from kgme.db.driver import build_client, get_graph


def _build_real_app() -> FastAPI:
    settings = load_settings()
    client = build_client(settings)
    graph = get_graph(client, settings=settings)
    anthropic_client = build_anthropic_client(settings)
    return create_app(
        client=client, graph=graph, anthropic_client=anthropic_client, settings=settings
    )


app = _build_real_app()
