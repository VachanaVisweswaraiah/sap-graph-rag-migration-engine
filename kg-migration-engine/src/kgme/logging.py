"""Structured JSON logging. The log is the GxP audit trail.

Kept at this path for backward compatibility; the real implementation lives in
`kgme.core.observability`.
"""

from __future__ import annotations

from kgme.core.observability import get_logger as get_logger

__all__ = ["get_logger"]
