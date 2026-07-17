"""Phase 0 smoke test: the package imports and version is exposed."""

from __future__ import annotations

import kgme


def test_version_present() -> None:
    assert kgme.__version__
