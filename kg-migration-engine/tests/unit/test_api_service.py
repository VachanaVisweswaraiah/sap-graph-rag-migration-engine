"""Unit tests for api/service.py's compute_module_impact — mocked graph, no DB."""

from __future__ import annotations

from unittest.mock import MagicMock

from kgme.api.service import compute_module_impact


def test_compute_module_impact_combines_counts_and_mapping_coverage() -> None:
    fake_graph = MagicMock()
    fake_graph.ro_query.side_effect = [
        MagicMock(result_set=[[8, 8, 0, 0]]),  # node counts query
        MagicMock(result_set=[[8, 0, []]]),  # mapping coverage query (compute_mapping_coverage)
        MagicMock(result_set=[]),  # s4 severity counts query -- no catalog rows matched
    ]

    impact = compute_module_impact(fake_graph, "governance")

    assert impact.module == "governance"
    assert impact.total_nodes == 8
    assert impact.gap_nodes == 8
    assert impact.peripheral_nodes == 0
    assert impact.documented_nodes == 0
    assert impact.mapping_coverage.total_transactions == 8
    assert impact.mapping_coverage.mapped_transactions == 0
    assert impact.s4_flagged_nodes == {}


def test_compute_module_impact_reports_s4_catalog_flags_grouped_by_severity() -> None:
    """s4_flagged_nodes must reflect whatever severity strings the catalog actually
    used for this module's matched nodes -- not a hardcoded set of severities."""
    fake_graph = MagicMock()
    fake_graph.ro_query.side_effect = [
        MagicMock(result_set=[[12, 0, 4, 8]]),
        MagicMock(result_set=[[12, 3, [["TX:MB1C", "TX:MIGO"]]]]),
        MagicMock(
            result_set=[
                ["Functional Gap (Process will break)", 9],
                ["Hard Stop (Conversion will fail)", 2],
            ]
        ),
    ]

    impact = compute_module_impact(fake_graph, "MM")

    assert impact.s4_flagged_nodes == {
        "Functional Gap (Process will break)": 9,
        "Hard Stop (Conversion will fail)": 2,
    }
