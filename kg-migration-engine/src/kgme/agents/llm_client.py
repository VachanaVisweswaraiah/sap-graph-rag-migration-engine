"""Anthropic client factory. Mirrors db/driver.py's build_client — one chokepoint
for constructing the client, api_key sourced from Settings."""

from __future__ import annotations

import anthropic

from kgme.config import Settings


def build_anthropic_client(settings: Settings) -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)
