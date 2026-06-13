export const meta = {
  name: 'improve-loop',
  description: 'Autonomous benchmark-driven SDK improvement loop: per wave, diagnose the worst gating check, implement the smallest legitimate fix, re-bench, and keep it only if it ratchets up (else revert) — committing kept waves and recording the improve/ ratchet. When a bench is READY, harden it to expose the next gap.',
  phases: [
    { title: 'Baseline' },
    { title: 'Waves' },
  ],
}

// ── inputs ───────────────────────────────────────────────────────────────────
// args = { bench, waves, model, label, harden, noImprovementStop }
const BENCHES = ['skillbench', 'taskbench', 'agentbench', 'extensionbench', 'coding-agent-bench']
const bench = (args && args.bench) || 'skillbench'
if (!BENCHES.includes(bench)) throw new Error(`unknown bench '${bench}' — one of ${BENCHES.join(', ')}`)
const waves = (args && args.waves) || 3
const model = (args && args.model) || ''
const label = (args && args.label) || 'loop'
const harden = args && args.harden === false ? false : true       // default: ratchet (harden when green)
const stopAfter = (args && args.noImprovementStop) || 2            // consecutive no-improvement waves → stop

const RUN = `python benchmarks/${bench}/run.py --live --report${model ? ` --model ${model}` : ''}` +
  (['skillbench', 'coding-agent-bench'].includes(bench) ? ' --trials 3' : '')
const BENCH_DIR = `benchmarks/${bench}`
const CLI = 'python3 benchmarks/_shared/improve_cli.py'

// ── structured returns ───────────────────────────────────────────────────────
const VERDICT = {
  type: 'object', additionalProperties: true,
  properties: {
    status: { type: 'string', enum: ['READY', 'NOT_READY', 'UNMEASURED'] },
    gates_pass: { type: 'number' }, gates_total: { type: 'number' },
    failing: { type: 'array', items: { type: 'string' } },
    snapshot_path: { type: 'string' },
  },
  required: ['status'],
}
const DIAGNOSIS = {
  type: 'object', additionalProperties: true,
  properties: {
    mode: { type: 'string', enum: ['fix', 'harden', 'stop'] },
    check: { type: 'string' }, root_cause: { type: 'string' },
    surface: { type: 'string' }, rationale: { type: 'string' },
  },
  required: ['mode'],
}
const IMPLEMENT = {
  type: 'object', additionalProperties: true,
  properties: {
    applied: { type: 'boolean' }, gates_green: { type: 'boolean' },
    summary: { type: 'string' }, reverted: { type: 'boolean' },
  },
  required: ['applied', 'gates_green'],
}
const DECISION = {
  type: 'object', additionalProperties: true,
  properties: { kept: { type: 'boolean' }, reason: { type: 'string' }, committed: { type: 'boolean' } },
  required: ['kept'],
}

// ── run a bench and snapshot its verdict deterministically ───────────────────
async function runBench(phase, lbl, outPath) {
  return agent(
    `Run from the packages/agent-sdk dir. Execute the live benchmark and capture its verdict deterministically:\n` +
    `  ( ${RUN} --label ${lbl} ; echo "EXIT=$?" ) 2>&1 | tee /tmp/${bench}-${lbl}.log\n` +
    `Then normalize the verdict to JSON:\n` +
    `  ${CLI} verdict-from-log /tmp/${bench}-${lbl}.log --out ${outPath}\n` +
    `Read ${outPath} and also list the FAILING gating checks (the \`FAIL …\` scorecard rows) from the log. ` +
    `Return the verdict object (status, gates_pass, gates_total, the failing check ids, and snapshot_path=${outPath}). ` +
    `Do not edit any bench, dataset, or SDK file in this step — only run and report.`,
    { schema: VERDICT, label: `bench:${lbl}`, phase },
  )
}

// ── the loop ─────────────────────────────────────────────────────────────────
phase('Baseline')
log(`improve-loop · ${bench} · up to ${waves} waves · harden=${harden}`)
let baseline = await runBench('Baseline', `${label}-w0`, `${BENCH_DIR}/improve/baseline.json`)
let dry = 0
const journal = []

for (let i = 1; i <= waves; i++) {
  if (dry >= stopAfter) { log(`stopping: ${dry} consecutive no-improvement waves`); break }
  const P = `Wave ${i}`
  phase(P)

  // allocate the append-only wave dir
  const waveInfo = await agent(
    `From packages/agent-sdk, run \`${CLI} wave-new ${BENCH_DIR}\` and return ONLY the wave id (e.g. wave-007) it prints.`,
    { label: `wave-new`, phase: P },
  )
  const wave = (waveInfo || '').trim().split(/\s+/)[0] || `wave-${i}`
  const waveDir = `${BENCH_DIR}/improve/${wave}`

  // 1. diagnose — decide fix vs harden vs stop, name the surface
  const diag = await agent(
    `You are diagnosing benchmark '${bench}' for the agent-sdk improve-loop (wave ${wave}). Baseline verdict: ` +
    `${JSON.stringify(baseline)}.\n` +
    `Use the optimize-verdict and bench-harden skills' knowledge (read .claude/skills/preact-bench/reference/` +
    `optimization-surfaces.md and .claude/skills/optimize-verdict/reference/verdict-to-surface.md).\n` +
    `- If status is NOT_READY/UNMEASURED: mode="fix". Pick the single highest-value FAILING gating check, read its ` +
    `probe trace / the --report HTML to name the root cause, and name the SMALLEST surface to tune ` +
    `(weight → registry row → plugin → prompt/skill content → runtime seam). ` +
    (harden
      ? `- If status is READY: mode="harden". Name the weakest dimension and a discriminating case to add (near-neighbor / refusal / adversarial / per-turn) per METHOD.md.\n`
      : `- If status is READY: mode="stop" (harden disabled).\n`) +
    `Write ${waveDir}/diagnosis.md (worst check, root cause, hot-spots) and ${waveDir}/rfc.md (the proposed minimal change). ` +
    `Return {mode, check, root_cause, surface, rationale}.`,
    { schema: DIAGNOSIS, label: `diagnose`, phase: P },
  )
  if (!diag || diag.mode === 'stop') { log(`wave ${wave}: nothing to do (${diag && diag.rationale}) — stopping`); break }

  // 2. implement — apply the change, keep the invariants green (or revert)
  const impl = await agent(
    `Implement wave ${wave} for '${bench}'. Plan (from ${waveDir}/rfc.md): mode=${diag.mode}, surface=${diag.surface}, ` +
    `target=${diag.check}.\n` +
    (diag.mode === 'fix'
      ? `Apply the SMALLEST legitimate SDK change via that surface (never weaken a gate, branch the interpreter, or stub a bench).`
      : `Add the discriminating dataset case to ${BENCH_DIR}/dataset/ (a harder bench that flips READY→NOT_READY is success, not regression). Do not touch SDK code.`) +
    `\nThen run the safety net: \`uv run python -m pytest -q\` and \`uv run ruff check agent_sdk && uv run ruff format agent_sdk\`. ` +
    `The five invariants (test_sdk_isolation, test_lobe_network parity, test_pinned_lobes_parity) MUST stay green. ` +
    `If anything goes red and you cannot keep it green, REVERT your change (git checkout/restore) and report applied=false, reverted=true. ` +
    `Save your change as ${waveDir}/diff.patch (\`git diff > ${waveDir}/diff.patch\`). ` +
    `Return {applied, gates_green, summary, reverted}.`,
    { schema: IMPLEMENT, label: `implement`, phase: P },
  )
  if (!impl || !impl.applied || !impl.gates_green) {
    log(`wave ${wave}: not applied / gates red — skipped`)
    journal.push({ wave, kept: false, reason: 'implement failed or gates red' })
    dry++
    continue
  }

  // 3. re-bench
  const after = await runBench(P, `${label}-${wave}`, `${waveDir}/after.json`)

  // 4. promote-or-revert — deterministic ratchet decision, then commit or revert
  const decision = await agent(
    `Decide whether to keep wave ${wave} of '${bench}'. Run the deterministic ratchet:\n` +
    `  ${CLI} promote ${BENCH_DIR} --wave ${wave} --after ${waveDir}/after.json --label ${label} ` +
    `${model ? `--model ${model} ` : ''}--note "${(diag.check || diag.mode).replace(/"/g, '')}"\n` +
    `It writes ${waveDir}/decision.json + updates improve/journal.md, improve/history.jsonl, and (if kept) improve/best.json; ` +
    `it exits 0 if kept, 1 if reverted.\n` +
    `- If KEPT: \`git add -A && git commit\` with a conventional subject — ` +
    `\`${diag.mode === 'harden' ? 'test' : 'fix'}(${bench}): ${diag.check || diag.surface} [${wave}]\` ` +
    `ending with the Co-Authored-By trailer. Set committed=true.\n` +
    `- If REVERTED: \`git restore .\` / \`git checkout -- .\` to drop the working-tree change (keep the append-only ${waveDir}/ record). committed=false.\n` +
    `Return {kept, reason, committed}.`,
    { schema: DECISION, label: `promote`, phase: P },
  )

  const kept = !!(decision && decision.kept)
  journal.push({ wave, mode: diag.mode, check: diag.check, kept, reason: decision && decision.reason })
  if (kept) { baseline = after; dry = 0; log(`wave ${wave}: KEPT (${diag.mode}) — ${decision.reason}`) }
  else { dry++; log(`wave ${wave}: reverted — ${decision && decision.reason}`) }
}

phase('Baseline')
log(`improve-loop done · ${bench} · final ${JSON.stringify(baseline)}`)
return { bench, final: baseline, waves: journal }
