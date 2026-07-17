"""Integration tests for db/loader.py against a real (testcontainers) FalkorDB.

Idempotency (test_load_is_idempotent) is the single most important test here.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from falkordb import FalkorDB

from kgme.config import Settings
from kgme.core.exceptions import KgmeError, LoadAbortedError, SchemaViolationError
from kgme.db import loader as loader_module
from kgme.db.driver import get_graph
from kgme.db.loader import StepResult, load_graph, wipe_graph
from tests.integration.conftest import DATA_DICTIONARY_PATH, EDGES_FIXTURE_PATH, NODES_FIXTURE_PATH

pytestmark = pytest.mark.integration


def _load(client: FalkorDB, settings: Settings, *, wipe: bool = False) -> list[StepResult]:
    return load_graph(
        client,
        settings=settings,
        nodes_path=NODES_FIXTURE_PATH,
        edges_path=EDGES_FIXTURE_PATH,
        data_dictionary_path=DATA_DICTIONARY_PATH,
        wipe=wipe,
    )


def test_load_creates_expected_counts(client: FalkorDB, settings: Settings) -> None:
    results = _load(client, settings)

    verify = next(r for r in results if r.step == "verify")
    assert verify.ok
    assert verify.summary["nodes"] == 5
    assert verify.summary["edges"] == 3


def test_load_is_idempotent(client: FalkorDB, settings: Settings) -> None:
    _load(client, settings)
    _load(client, settings)

    graph = get_graph(client, settings=settings)
    node_count = graph.query("MATCH (n:Entity) RETURN count(n)").result_set[0][0]
    edge_count = graph.query("MATCH ()-[r]->() RETURN count(r)").result_set[0][0]
    assert node_count == 5
    assert edge_count == 3

    duplicates = graph.query(
        "MATCH ()-[r]->() WITH r.edge_id AS eid, count(r) AS c WHERE c > 1 RETURN count(*)"
    ).result_set[0][0]
    assert duplicates == 0


def test_reload_with_corrected_edge_endpoint_replaces_not_duplicates(
    client: FalkorDB, settings: Settings, tmp_path: Path
) -> None:
    """Regression test: MERGE keys on (endpoints + edge_id), so a re-load that
    corrects an edge's source_id/target_id for the same edge_id must not leave the
    old, wrong-endpoint edge orphaned alongside the new, corrected one."""
    _load(client, settings)

    corrected_edges = tmp_path / "kg_edges_corrected.csv"
    original_rows = EDGES_FIXTURE_PATH.read_text(encoding="utf-8-sig").splitlines()
    header, first_row, *rest = original_rows
    fields = first_row.split(",")
    edge_id = fields[0]
    # Repoint this edge's target to a different real node in the fixture graph,
    # keeping the same edge_id -- simulates a hand-curated CSV correction.
    other_target = rest[0].split(",")[2] if rest else fields[2]
    fields[2] = other_target
    corrected_first_row = ",".join(fields)
    corrected_edges.write_text(
        "\n".join([header, corrected_first_row, *rest]) + "\n", encoding="utf-8"
    )

    load_graph(
        client,
        settings=settings,
        nodes_path=NODES_FIXTURE_PATH,
        edges_path=corrected_edges,
        data_dictionary_path=DATA_DICTIONARY_PATH,
    )

    graph = get_graph(client, settings=settings)
    matching = graph.query(
        "MATCH ()-[r {edge_id: $edge_id}]->() RETURN count(r)", {"edge_id": edge_id}
    ).result_set[0][0]
    assert matching == 1, "corrected edge_id must replace the old edge, not duplicate it"

    target_ids = graph.query(
        "MATCH (s)-[r {edge_id: $edge_id}]->(t) RETURN t.node_id", {"edge_id": edge_id}
    ).result_set
    assert target_ids[0][0] == other_target


def test_every_node_has_provenance(client: FalkorDB, settings: Settings) -> None:
    _load(client, settings)
    graph = get_graph(client, settings=settings)

    missing = graph.query(
        "MATCH (n:Entity) WHERE n.confidence IS NULL OR n.source_doc IS NULL RETURN count(n)"
    ).result_set[0][0]
    assert missing == 0


def test_every_edge_has_provenance(client: FalkorDB, settings: Settings) -> None:
    _load(client, settings)
    graph = get_graph(client, settings=settings)

    missing = graph.query(
        "MATCH ()-[r]->() WHERE r.confidence IS NULL OR r.source_doc IS NULL RETURN count(r)"
    ).result_set[0][0]
    assert missing == 0


def test_no_dangling_relationship_endpoints(client: FalkorDB, settings: Settings) -> None:
    results = _load(client, settings)
    verify = next(r for r in results if r.step == "verify")
    # Every edge in the fixture CSV made it into the graph as a relationship —
    # nothing was silently dropped for a missing endpoint.
    assert verify.summary["edges"] == 3


def test_uniqueness_constraint_enforced(client: FalkorDB, settings: Settings) -> None:
    _load(client, settings)
    graph = get_graph(client, settings=settings)

    constraints = graph.list_constraints()
    assert any(
        c["label"] == "Entity" and "node_id" in c["properties"] and c["type"] == "UNIQUE"
        for c in constraints
    )


def test_labels_promoted(client: FalkorDB, settings: Settings) -> None:
    _load(client, settings)
    graph = get_graph(client, settings=settings)

    labels = graph.query("MATCH (n:Entity {node_id: 'PROC:MM01'}) RETURN labels(n)").result_set[0][
        0
    ]
    assert "BusinessProcess" in labels


def test_critical_step_failure_aborts_pipeline(
    client: FalkorDB, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_load_nodes_step(graph, nodes):  # type: ignore[no-untyped-def]
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(loader_module, "_load_nodes_step", broken_load_nodes_step)

    with pytest.raises(LoadAbortedError):
        _load(client, settings)

    graph = get_graph(client, settings=settings)
    # load_edges/promote_labels/verify never ran: no relationships were created.
    edge_count = graph.query("MATCH ()-[r]->() RETURN count(r)").result_set[0][0]
    assert edge_count == 0


def test_wipe_requires_explicit_confirmation(client: FalkorDB, settings: Settings) -> None:
    graph = get_graph(client, settings=settings)
    with pytest.raises(KgmeError):
        wipe_graph(graph, wipe=True)


def test_wipe_with_confirmation_succeeds(
    client: FalkorDB, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KGME_ALLOW_WIPE", "1")
    _load(client, settings)
    graph = get_graph(client, settings=settings)

    wipe_graph(graph, wipe=True)

    graph = get_graph(client, settings=settings)
    count = graph.query("MATCH (n) RETURN count(n)").result_set[0][0]
    assert count == 0


def test_unlisted_node_type_raises_schema_violation(
    client: FalkorDB, settings: Settings, tmp_path: Path
) -> None:
    bad_nodes = tmp_path / "kg_nodes_bad.csv"
    bad_nodes.write_text(
        "node_id,node_type,label,module,gxp_classification,confidence,source_doc,source_ref,notes\n"
        "PROC:BAD01,NotARealType,Bad,MM,unkritisch,documented,FIXTURE,test,\n",
        encoding="utf-8",
    )

    with pytest.raises(SchemaViolationError):
        load_graph(
            client,
            settings=settings,
            nodes_path=bad_nodes,
            edges_path=EDGES_FIXTURE_PATH,
            data_dictionary_path=DATA_DICTIONARY_PATH,
        )

    graph = get_graph(client, settings=settings)
    count = graph.query("MATCH (n) RETURN count(n)").result_set[0][0]
    assert count == 0
