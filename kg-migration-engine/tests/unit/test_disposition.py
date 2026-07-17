"""Unit tests for enrichment/disposition.py — pure logic, no DB.

Golden cases use the *actual real strings* found in data/raw/kg_nodes.csv and
data/raw/kg_process_master.csv (verified by direct inspection during planning), not
just synthetic examples, per CLAUDE.md's working agreement on the disposition parser.
"""

from __future__ import annotations

from pathlib import Path

from kgme.enrichment.disposition import (
    DispositionFact,
    UnparsedEntry,
    extract_key_transaction_codes,
    load_dispositions,
    parse_node_notes,
    parse_process_disposition,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
NODES_PATH = REPO_ROOT / "data" / "raw" / "kg_nodes.csv"
PROCESS_MASTER_PATH = REPO_ROOT / "data" / "raw" / "kg_process_master.csv"


def test_extract_key_transaction_codes_strips_wrapping_parens_and_slash_separators() -> None:
    codes = extract_key_transaction_codes("(MB1A/MB1B/MBRL via Basisrolle)")
    assert codes == ["MB1A", "MB1B", "MBRL"]


def test_extract_key_transaction_codes_semicolon_separated() -> None:
    assert extract_key_transaction_codes("MB1C;ZMB90;MIGO") == ["MB1C", "ZMB90", "MIGO"]


def test_extract_key_transaction_codes_does_not_expand_range() -> None:
    codes = extract_key_transaction_codes("F-90;ABUMN;ABT1N;AFAB;AS01-26;AW01N;AJAB")
    assert "AS01" not in codes
    assert "AS01-26" not in codes
    assert codes == ["ABUMN", "ABT1N", "AFAB", "AW01N", "AJAB"]


def test_extract_key_transaction_codes_handles_placeholder() -> None:
    assert extract_key_transaction_codes("-") == []


def test_parse_node_notes_ignores_cells_not_starting_with_s4_prefix() -> None:
    assert parse_node_notes("TX:ZMB90", "Spezifikation fehlt (gap)") is None
    assert parse_node_notes("TX:ZFI_DELAFABER", "Sunset-Kandidat; Spec fehlt") is None


def test_parse_node_notes_abgeschaltet_with_arrow_real_string() -> None:
    fact = parse_node_notes("TX:MB1C", "S/4: abgeschaltet -> MIGO/Fiori")
    assert isinstance(fact, DispositionFact)
    assert fact.kind == "migrates_to"
    assert fact.source_node_id == "TX:MB1C"
    assert fact.target_node_id == "TX:MIGO"  # not "TX:MIGO/Fiori"
    assert fact.source_ref == "kg_nodes.csv:TX:MB1C.notes"


def test_parse_node_notes_abgeschaltet_alone_real_string() -> None:
    fact = parse_node_notes("TX:MB1A", "S/4: abgeschaltet")
    assert isinstance(fact, DispositionFact)
    assert fact.kind == "status_only"
    assert fact.status == "deprecated"
    assert fact.target_node_id is None


def test_parse_node_notes_zentrale_ta_real_string() -> None:
    fact = parse_node_notes("TX:MIGO", "S/4: zentrale TA")
    assert isinstance(fact, DispositionFact)
    assert fact.kind == "status_only"
    assert fact.status == "central"


def test_parse_node_notes_unrecognized_s4_prefix_is_unparsed() -> None:
    result = parse_node_notes("TX:FAKE", "S/4: something we've never seen")
    assert isinstance(result, UnparsedEntry)


def test_parse_process_disposition_literal_arrow_real_string() -> None:
    facts = parse_process_disposition(
        "MM01", "Zwangsumbau (MB1C->MIGO) + Revalidierung", "MB1C;ZMB90;MIGO"
    )
    assert isinstance(facts, list)
    assert len(facts) == 1
    assert facts[0].kind == "migrates_to"
    assert facts[0].source_node_id == "TX:MB1C"
    assert facts[0].target_node_id == "TX:MIGO"


def test_parse_process_disposition_wildcard_resolves_against_key_transactions() -> None:
    facts = parse_process_disposition(
        "MM03", "Zwangsumbau (MB1*->MIGO)", "(MB1A/MB1B/MBRL via Basisrolle)"
    )
    assert isinstance(facts, list)
    resolved = {f.source_node_id for f in facts}
    assert resolved == {"TX:MB1A", "TX:MB1B"}  # MBRL correctly excluded
    assert all(f.target_node_id == "TX:MIGO" for f in facts)


def test_parse_process_disposition_wildcard_with_no_matches_is_unparsed() -> None:
    result = parse_process_disposition("MMXX", "Zwangsumbau (ZZ*->MIGO)", "MB1C;ZMB90;MIGO")
    assert isinstance(result, UnparsedEntry)


def test_parse_process_disposition_bleibt_real_string() -> None:
    facts = parse_process_disposition("MM02", "Zwangsumbau (MIGO bleibt)", "MIGO")
    assert isinstance(facts, list)
    assert len(facts) == 1
    assert facts[0].kind == "status_only"
    assert facts[0].status == "unchanged"
    assert facts[0].source_node_id == "TX:MIGO"


def test_parse_process_disposition_conceptual_note_is_unparsed_real_string() -> None:
    result = parse_process_disposition(
        "AM01",
        "Zwangsumbau (New Asset Accounting; Ledger-Entscheidung)",
        "F-90;ABUMN;ABT1N;AFAB;AS01-26;AW01N;AJAB",
    )
    assert isinstance(result, UnparsedEntry)


def test_parse_process_disposition_offen_is_unparsed() -> None:
    result = parse_process_disposition("MM04", "offen (Doku fehlt)", "-")
    assert isinstance(result, UnparsedEntry)


def test_parse_process_disposition_synergie_is_unparsed() -> None:
    result = parse_process_disposition("MM26", "Synergie (xSuite-Integration)", "-")
    assert isinstance(result, UnparsedEntry)


def test_load_dispositions_against_real_files() -> None:
    facts, unparsed = load_dispositions(NODES_PATH, PROCESS_MASTER_PATH)

    # The sanitized portfolio data keeps only generic "Forced redesign" placeholders.
    # Those are disposition attempts, but not parseable transaction-level facts, so
    # the parser must fail closed and report them as unparsed.
    migrates = [f for f in facts if f.kind == "migrates_to"]
    statuses = [f for f in facts if f.kind == "status_only"]
    assert len(migrates) == 0
    assert len(statuses) == 0
    assert len(unparsed) == 8
    assert {entry.raw_text for entry in unparsed} == {"Forced redesign (sample transaction)"}
