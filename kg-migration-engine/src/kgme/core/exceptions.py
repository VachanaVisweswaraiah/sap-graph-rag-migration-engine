"""Typed error hierarchy. Every DB/loader failure is one of these — no bare exceptions,
no silent swallowing. A GxP graph must fail fast and loud, never guess."""

from __future__ import annotations


class KgmeError(Exception):
    """Base class for all application-raised errors."""


class ConnectionUnavailableError(KgmeError):
    """The graph database could not be reached."""


class LoadAbortedError(KgmeError):
    """A load step failed and the pipeline was halted before completing."""


class SchemaViolationError(KgmeError):
    """A CSV row used a node_type or relation value not in kg_data_dictionary.csv."""


class ProvenanceViolationError(KgmeError):
    """Reserved for a future stricter provenance check."""


class CypherGuardViolationError(KgmeError):
    """A write-capable Cypher query was blocked before execution. A write-capable
    query reaching an agent path is a defect, not a nit — this must never be
    silently swallowed."""
