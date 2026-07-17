"""Session-scoped FalkorDB test container + per-test isolated graph fixtures.

Each test gets its own uniquely-named graph (client.select_graph(f"test_...")) rather
than wipe-and-reuse against one shared database — FalkorDB graphs are named and cheap
to create/drop, so there's no shared-state risk between tests.
"""

from __future__ import annotations

import contextlib
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from falkordb import FalkorDB
from testcontainers.core.container import DockerContainer

from kgme.config import Settings
from kgme.db.driver import build_client, close_client

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"
NODES_FIXTURE_PATH = FIXTURES_DIR / "kg_nodes_fixture.csv"
EDGES_FIXTURE_PATH = FIXTURES_DIR / "kg_edges_fixture.csv"
DATA_DICTIONARY_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "raw" / "kg_data_dictionary.csv"
)

_FALKORDB_PASSWORD = "kgme-test-password"  # test-only, throwaway container


def _wait_for_ping(container: DockerContainer, *, timeout_s: float = 30.0) -> None:
    host = container.get_container_host_ip()
    port = int(container.get_exposed_port(6379))
    deadline = time.monotonic() + timeout_s
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            client = FalkorDB(host=host, port=port, password=_FALKORDB_PASSWORD)
            client.connection.ping()
            client.close()
            return
        except Exception as exc:  # retry on any connection error until timeout
            last_error = exc
            time.sleep(0.5)
    raise RuntimeError(f"FalkorDB container did not become ready in time: {last_error}")


@pytest.fixture(scope="session")
def falkordb_container() -> Iterator[DockerContainer]:
    container = (
        DockerContainer("falkordb/falkordb:latest")
        .with_exposed_ports(6379)
        .with_env("REDIS_ARGS", f"--requirepass {_FALKORDB_PASSWORD} --appendonly no")
    )
    with container:
        _wait_for_ping(container)
        yield container


@pytest.fixture
def settings(falkordb_container: DockerContainer) -> Settings:
    """A fresh, uniquely-named graph per test — the test-isolation mechanism."""
    host = falkordb_container.get_container_host_ip()
    port = int(falkordb_container.get_exposed_port(6379))
    graph_name = f"test_{uuid.uuid4().hex[:12]}"
    return Settings(
        falkordb_host=host,
        falkordb_port=port,
        falkordb_password=_FALKORDB_PASSWORD,
        falkordb_graph=graph_name,
        anthropic_api_key="test-not-used",
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[FalkorDB]:
    kgme_client = build_client(settings)
    yield kgme_client
    graph = kgme_client.select_graph(settings.falkordb_graph)
    with contextlib.suppress(Exception):
        graph.delete()
    close_client(kgme_client)
