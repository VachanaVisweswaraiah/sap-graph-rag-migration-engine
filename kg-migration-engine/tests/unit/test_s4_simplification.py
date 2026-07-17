"""Unit tests for enrichment/s4_simplification.py — pure logic, no DB."""

from __future__ import annotations

from pathlib import Path

from kgme.enrichment.s4_simplification import (
    CatalogRow,
    build_enrichment_properties,
    extract_codes,
    load_catalog,
    match_catalog_to_nodes,
)

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "s4_catalog_fixture.json"


def _row(**overrides: str) -> CatalogRow:
    defaults: dict[str, str] = {
        "simplification_item_id": "SI X",
        "ecc_object_type": "Transaction",
        "ecc_object_name": "FIX01",
        "s4hana_status": "Deprecated",
        "s4hana_target": "MIGO",
        "sap_note_reference": "1234567",
        "remediation_category": "Custom Code",
        "severity": "Functional Gap (Process will break)",
        "actionable_recommendation": "Do the thing.",
    }
    defaults.update(overrides)
    return CatalogRow(**defaults)


def test_extract_codes_simple_multivalue() -> None:
    assert extract_codes("AB01, ABNA, ABMA") == ["AB01", "ABNA", "ABMA"]


def test_extract_codes_strips_parenthetical_notes() -> None:
    assert extract_codes("AB01, ABNA, ABZO (non-L posting transactions)") == [
        "AB01",
        "ABNA",
        "ABZO",
    ]


def test_extract_codes_ignores_descriptive_prefixes() -> None:
    # "BAdI" and "user"/"exits" are not SAP-code-shaped (mixed/lowercase) and are dropped.
    codes = extract_codes("A_M_ANLKL; BAdI BADI_FIAA_DOCLINES; user exits AFAR0004")
    assert codes == ["A_M_ANLKL", "BADI_FIAA_DOCLINES", "AFAR0004"]


def test_extract_codes_does_not_expand_ranges() -> None:
    # A dash-joined range is never expanded into individual codes — it's dropped
    # entirely rather than guessed at (fails closed).
    codes = extract_codes("ABST, AUN1-AUN11, AR16")
    assert codes == ["ABST", "AR16"]
    assert "AUN1" not in codes
    assert "AUN11" not in codes


def test_extract_codes_does_not_expand_slash_notation() -> None:
    codes = extract_codes("XD01/02/03, FD06")
    assert codes == ["FD06"]


def test_extract_codes_dedupes() -> None:
    assert extract_codes("AB01, AB01, ABNA") == ["AB01", "ABNA"]


def test_load_catalog_parses_fixture() -> None:
    rows = load_catalog(FIXTURE_PATH)
    assert len(rows) == 1
    assert rows[0].ecc_object_type == "Transaction"
    assert rows[0].ecc_object_name == "MM01_MAIN"


def test_match_catalog_to_nodes_matches_existing_transaction() -> None:
    rows = load_catalog(FIXTURE_PATH)
    existing = frozenset({"TX:MM01_MAIN", "PROC:MM01_MAIN"})

    result = match_catalog_to_nodes(rows, existing)

    assert len(result.matched) == 1
    matched_row, node_id = result.matched[0]
    assert node_id == "TX:MM01_MAIN"
    assert matched_row.simplification_item_id == "SIM0001"


def test_match_catalog_to_nodes_reports_unmatched_code() -> None:
    rows = load_catalog(FIXTURE_PATH)
    existing = frozenset[str]()

    result = match_catalog_to_nodes(rows, existing)

    unmatched_codes = [code for _row, code in result.unmatched_codes]
    assert "MM01_MAIN" in unmatched_codes


def test_match_catalog_to_nodes_skips_program_and_concept_rows() -> None:
    rows = [_row(ecc_object_type="Program"), _row(ecc_object_type="Concept")]
    existing = frozenset({"TX:FIX01"})

    result = match_catalog_to_nodes(rows, existing)

    skipped_types = {row.ecc_object_type for row in result.skipped_rows}
    assert skipped_types == {"Program", "Concept"}
    assert len(result.skipped_rows) == 2


def test_build_enrichment_properties_is_namespaced_and_inferred() -> None:
    row = _row()
    props = build_enrichment_properties(row)

    assert props["s4_status"] == "Deprecated"
    assert props["s4_target"] == "MIGO"
    assert props["s4_confidence"] == "inferred"
    assert props["s4_source_doc"] == "DERIVED:SAP_SIMPLIFICATION_LIST"
    assert "SI X" in props["s4_source_ref"]
    assert "1234567" in props["s4_source_ref"]
    # Must never touch the node's own top-level provenance fields.
    assert "confidence" not in props
    assert "source_doc" not in props
    assert "source_ref" not in props
