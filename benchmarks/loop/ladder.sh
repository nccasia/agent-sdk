#!/usr/bin/env bash
# improve-loop ladder — one full sweep of the readiness ladder for the feedback loop.
#
# Runs the free deterministic gate, then every live bench, capturing each verdict so the loop can
# see the SDK getting "better and better" across iterations. NON-INVASIVE: it shells out to each
# bench and records exit code + scorecard; it never edits a bench or a dataset. The improving is
# done by the optimize-verdict / bench-harden skills, not here.
#
# Usage (from anywhere — it cd's to the package):
#   bash benchmarks/loop/ladder.sh
# Env knobs (for comparable numbers across iterations):
#   LOOP_MODEL=<id>     pin the provider model (skillbench/agentbench/extensionbench)
#   LOOP_TRIALS=<n>     trials for variance-pooling benches (skillbench/coding-agent-bench; default 3)
#   LOOP_FREE_ONLY=1    run only the free gate (skip the live benches)
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 2          # → packages/agent-sdk

OUT="benchmarks/loop/last-run"
rm -rf "$OUT"; mkdir -p "$OUT"
TRIALS="${LOOP_TRIALS:-3}"
MODEL_ARGS=(); [[ -n "${LOOP_MODEL:-}" ]] && MODEL_ARGS=(--model "$LOOP_MODEL")

echo "── free gate ─────────────────────────────────────────────────"
bash benchmarks/ci-free-gates.sh > "$OUT/free-gate.log" 2>&1
echo $? > "$OUT/free-gate.exit"
tail -1 "$OUT/free-gate.log"
if [[ "$(cat "$OUT/free-gate.exit")" != "0" ]]; then
  echo "free gate RED — stopping the ladder (fix the unit/invariant gate first)." >&2
  python3 benchmarks/loop/snapshot.py "$OUT"
  exit 1
fi
[[ "${LOOP_FREE_ONLY:-}" == "1" ]] && { python3 benchmarks/loop/snapshot.py "$OUT"; exit 0; }

# Each bench takes a different flag set — pass only what it accepts (see benchmarks/loop README note).
run () { local name="$1"; shift; echo "── $name ─────────────"; \
         python3 "benchmarks/$name/run.py" "$@" > "$OUT/$name.log" 2>&1; echo $? > "$OUT/$name.exit"; \
         grep -aE "verdict (READY|NOT_READY|UNMEASURED)" "$OUT/$name.log" | tail -1 || tail -1 "$OUT/$name.log"; }

# free / deterministic benches (no provider needed — they read the engine's pure functions)
run flowbench          --report
run attentionbench     --report
# promptbench: free structure+quality tiers (READY with no creds) + an opt-in LLM-judge tier.
run promptbench        --live --report "${MODEL_ARGS[@]}"
# live benches (real provider). corgictionbech keeps a deterministic floor (READY with no creds)
# and adds a single-arm live measurement of the equipped agent when a provider is present.
run corgictionbech     --live --report "${MODEL_ARGS[@]}"
run skillbench         --live --report --trials "$TRIALS" "${MODEL_ARGS[@]}"
run toolbench          --live --report "${MODEL_ARGS[@]}"
run taskbench          --live
run agentbench         --live --report "${MODEL_ARGS[@]}"
run extensionbench     --live --report "${MODEL_ARGS[@]}"
run coding-agent-bench --live --trials "$TRIALS"

echo
python3 benchmarks/loop/snapshot.py "$OUT"
