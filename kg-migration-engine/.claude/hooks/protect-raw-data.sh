#!/usr/bin/env bash
# PreToolUse (Write|Edit) guardrail: refuse any write to pristine source data or secrets.
# Exit 2 = deny the tool call. GxP: raw source data must never be mutated by the agent.
# data/external/ holds downloaded third-party reference artifacts (e.g. the SAP
# Simplification List) - also immutable once placed, same rationale as data/raw/.
path="${CLAUDE_FILE_PATH:-}"
case "$path" in
  *"/data/raw/"*|*"/data/external/"*|*/.env|*/.env.*)
    echo "BLOCKED: writing to protected path '$path' is not allowed (GxP: source data & secrets are immutable)." >&2
    exit 2
    ;;
esac
exit 0
