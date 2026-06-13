#!/usr/bin/env bash
# Deterministic code safety net for the agent-sdk — the unit suite (no provider, no network).
# The benches are all LIVE (benchmarks/agentbench, coding-agent-bench, extensionbench) — run on
# demand with --live, never in CI. Plugin plug/unplug *structure* is in the unit suite
# (tests/test_plugins_full_surface.py); the live behavior is benchmarks/extensionbench --live.
set -uo pipefail

cd "$(dirname "$0")/../../.."   # repo root
SDK="packages/agent-sdk"
FAIL=0
run() { "$@" || { echo "GATE FAILED: $*" >&2; FAIL=1; }; }

echo "── unit: agent-sdk ───────────────────────────────────────────"
run uv --directory "$SDK" run python -m pytest -q

if [[ "$FAIL" -ne 0 ]]; then
  echo; echo "CI: FAILED" >&2; exit 1
fi
echo; echo "CI: all green"
