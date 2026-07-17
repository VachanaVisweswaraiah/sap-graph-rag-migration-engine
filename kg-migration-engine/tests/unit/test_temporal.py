"""NOTE: This file has been sanitized for public/private portfolio use.
Business logic, domain-specific rules, and proprietary details have been masked.
The coding patterns, architecture, and technical implementation remain authentic.
[MASKED] tags indicate where original business logic has been replaced.

Unit tests for agents/temporal.py — a fixed, invariant answer (no graph, no
LLM), since the graph has no date property to query."""

from __future__ import annotations

from kgme.agents.temporal import TEMPORAL_ANSWER, answer_temporal_question


def test_answer_temporal_question_returns_the_constant() -> None:
    assert answer_temporal_question() == TEMPORAL_ANSWER


def test_temporal_answer_states_no_date_property_exists() -> None:
    assert "does not track document dates" in TEMPORAL_ANSWER


def test_temporal_answer_cites_the_two_known_real_date_ranges() -> None:
    assert "2005-2007" in TEMPORAL_ANSWER
    assert "2014-2025" in TEMPORAL_ANSWER
