"""Unit tests for db/driver.py — mocked FalkorDB client, no Docker/network."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

import kgme.db.driver as driver_module
from kgme.config import Settings
from kgme.core.exceptions import ConnectionUnavailableError
from kgme.db.driver import AccessMode, build_client, get_graph, read_only_query, run_query


def _settings() -> Settings:
    return Settings(falkordb_password="pw", anthropic_api_key="key")


def test_build_client_verifies_connectivity(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = MagicMock()
    monkeypatch.setattr(driver_module, "FalkorDB", MagicMock(return_value=fake_client))

    result = build_client(_settings())

    assert result is fake_client
    fake_client.connection.ping.assert_called_once()


def test_build_client_raises_on_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = MagicMock()
    fake_client.connection.ping.side_effect = ConnectionError("refused")
    monkeypatch.setattr(driver_module, "FalkorDB", MagicMock(return_value=fake_client))

    with pytest.raises(ConnectionUnavailableError):
        build_client(_settings())


def test_get_graph_selects_configured_graph_name() -> None:
    fake_client = MagicMock()
    settings = _settings()

    get_graph(fake_client, settings=settings)

    fake_client.select_graph.assert_called_once_with(settings.falkordb_graph)


def test_run_query_write_mode_calls_query() -> None:
    fake_graph = MagicMock()

    run_query(fake_graph, "RETURN 1", {"a": 1}, mode=AccessMode.WRITE)

    fake_graph.query.assert_called_once_with("RETURN 1", {"a": 1})
    fake_graph.ro_query.assert_not_called()


def test_run_query_read_mode_calls_ro_query() -> None:
    fake_graph = MagicMock()

    run_query(fake_graph, "RETURN 1", mode=AccessMode.READ)

    fake_graph.ro_query.assert_called_once_with("RETURN 1", None)
    fake_graph.query.assert_not_called()


def test_read_only_query_defaults_to_ro_query() -> None:
    fake_graph = MagicMock()

    read_only_query(fake_graph, "MATCH (n) RETURN n")

    fake_graph.ro_query.assert_called_once_with("MATCH (n) RETURN n", None)
    fake_graph.query.assert_not_called()


def test_run_query_defaults_to_write_mode() -> None:
    fake_graph: Any = MagicMock()

    run_query(fake_graph, "RETURN 1")

    fake_graph.query.assert_called_once_with("RETURN 1", None)
