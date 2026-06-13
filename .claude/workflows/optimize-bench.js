export const meta = {
  name: 'optimize-bench',
  description: 'Comprehensive per-bench optimize loop: baseline (free + live LLM) → grow realistic + adversarial scenarios → diagnose the worst gating check → implement the smallest legitimate SDK fix → re-bench → ratchet (keep/revert, commit kept). Each round makes the bench AND the SDK better, on real provider output, until the bench converges. The flagship driver behind /optimize-bench.',
  phases: [
    { title: 'Baseline' },
    { title: 'Rounds' },
  ],
}

// ── inputs ───────────────────────────────────────────────────────────────────
// args = { bench, rounds, model, grow, live, label }
const BENCHES = ['skillbench', 'toolbench', 'taskbench', 'agentbench', 'extensionbench',
  'coding-agent-bench', 'attentionbench', 'flowbench', 'corgictionbech']
const FREE_ONLY = new Set(['attentionbench', 'flowbench', 'corgictionbech'])  // no live tier
const MODEL_OK = new Set(['skillbench', 'toolbench', 'agentbench', 'extensionbench'])
const TRIALS = new Set(['skillbench', 'coding-agent-bench'])

const bench = (args && args.bench) || 'skillbench'
if (!BENCHES.includes(bench)) throw new Error(`unknown bench '${bench}' — one of ${BENCHES.join(', ')}`)
const rounds = (args && args.rounds) || 4
const model = (args && args.model) || ''
const grow = args && args.grow === false ? false : true
const live = args && args.live === false ? false : true
const label = (args && args.label) || 'opt'

const BENCH_DIR = `benchmarks/${bench}`
const CLI = 'python3 benchmarks/_shared/improve_cli.py'
const useLive = !FREE_ONLY.has(bench) && live

// build the bench run command for a given label (only the flags this bench accepts)
function runCmd(lbl) {
  const f = []
  if (useLive) f.push('--live')
  f.push('--report')
  if (TRIALS.has(bench)) f.push('--trials', '3')
  if (model && MODEL_OK.has(bench)) f.push('--model', model)
  f.push('--label', lbl)
  return `python benchmarks/${bench}/run.py ${f.join(' ')}`
}

// ── structured returns ───────────────────────────────────────────────────────
const VERDICT = {
  type: 'object', additionalProperties: true,
  properties: {
    status: { type: 'string', enum: ['READY', 'NOT_READY', 'UNMEASURED'] },
    gates_pass: { type: 'number' }, gates_total: { type: 'number' },
    failing: { type: 'array', items: { type: 'string' } }, snapshot_path: { type: 'string' },
  },
  required: ['status'],
}
const GROW = {
  type: 'object', additionalProperties: true,
  properties: {
    added: { type: 'number' }, bites: { type: 'boolean' },
    categories: { type: 'array', items: { type: 'string' } }, summary: { type: 'string' },
  },
  required: ['added'],
}
const DIAGNOSIS = {
  type: 'object', additionalProperties: true,
  properties: {
    mode: { type: 'string', enum: ['fix', 'converged'] },
    check: { type: 'string' }, root_cause: { type: 'string' }, surface: { type: 'string' },
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

async function runBench(phase, lbl, outPath) {
  return agent(
    `Run from packages/agent-sdk. Execute the benchmark and snapshot its verdict deterministically:\n` +
    `  ( ${runCmd(lbl)} ; echo "EXIT=$?" ) 2>&1 | tee /tmp/${bench}-${lbl}.log\n` +
    `  ${CLI} verdict-from-log /tmp/${bench}-${lbl}.log --out ${outPath}\n` +
    `Read ${outPath} and list the FAILING gating checks (the \`FAIL …\` scorecard rows). Return the ` +
    `verdict (status, gates_pass, gates_total, failing[], snapshot_path=${outPath}). Run/report only — ` +
    `do not edit any bench, dataset, or SDK file in this step.`,
    { schema: VERDICT, label: `bench:${lbl}`, phase },
  )
}

async function newWave(phase) {
  const out = await agent(
    `From packages/agent-sdk, run \`${CLI} wave-new ${BENCH_DIR}\` and return ONLY the wave id (e.g. wave-007).`,
    { label: 'wave-new', phase },
  )
  return (out || '').trim().split(/\s+/)[0] || 'wave-000'
}

// ── the loop ─────────────────────────────────────────────────────────────────
phase('Baseline')
log(`optimize-bench · ${bench} · rounds=${rounds} · grow=${grow} · live=${useLive}`)
const gate = await agent(
  `From packages/agent-sdk run \`bash benchmarks/ci-free-gates.sh\` and report whether it is GREEN. ` +
  `Return just "GREEN" or "RED: <reason>". Do not fix anything.`,
  { label: 'free-gate', phase: 'Baseline' })
if (/^RED/i.test((gate || '').trim())) {
  log(`free gate RED — stopping (fix the unit/invariant gate first): ${gate}`)
  return { bench, stopped: 'free-gate-red', detail: gate }
}
let baseline = await runBench('Baseline', `${label}-w0`, `${BENCH_DIR}/improve/baseline.json`)
const journal = []

for (let i = 1; i <= rounds; i++) {
  const P = `Round ${i}`
  phase(P)

  // 1. GROW — author realistic + adversarial scenarios, confirm they bite, re-baseline
  if (grow) {
    const wave = await newWave(P)
    const g = await agent(
      `Grow benchmark '${bench}' (wave ${wave}) with NEW scenarios — the "add more data" half of the loop. ` +
      `Read .claude/skills/optimize-bench/reference/scenario-templates.md (the per-bench scenario surface + ` +
      `category templates) and ${BENCH_DIR}/METHOD.md.\n` +
      `Author 1–3 REALISTIC + 1–2 ADVERSARIAL scenarios (near-neighbor / refusal / edge / per-turn) that a ` +
      `correct SDK SHOULD satisfy, and add them to this bench's scenario surface (a dataset/*.jsonl line, or ` +
      `the inline SCN list for attentionbench/flowbench/corgictionbech). Then re-run the bench ` +
      `(\`${runCmd(`${label}-${wave}`)}\`) to see whether the new cases BITE (flip a gating check to FAIL). ` +
      `Keep the free gate + invariants green. Write ${BENCH_DIR}/improve/${wave}/diagnosis.md noting what you ` +
      `added and whether it bit. Return {added, bites, categories[], summary}.`,
      { schema: GROW, label: 'grow', phase: P })
    const added = (g && g.added) || 0
    if (added > 0) {
      const after = await runBench(P, `${label}-${wave}`, `${BENCH_DIR}/improve/${wave}/after.json`)
      await agent(
        `Record the dataset-growth wave for '${bench}'. Run:\n` +
        `  ${CLI} promote ${BENCH_DIR} --wave ${wave} --after ${BENCH_DIR}/improve/${wave}/after.json ` +
        `--label ${label} --kind dataset --scenarios-added ${added} --note "grow: ${(g.categories || []).join('/')}"\n` +
        `(dataset growth always re-baselines — it never reverts the scenarios.) Then ` +
        `\`git add -A && git commit\` with subject \`test(${bench}): grow scenarios [${wave}]\` ending with the ` +
        `Co-Authored-By trailer. Return {kept:true, committed:true}.`,
        { schema: DECISION, label: 'grow-commit', phase: P })
      baseline = after
      journal.push({ wave, kind: 'dataset', added, bites: !!(g && g.bites) })
      log(`round ${i}: grew +${added} scenarios (${g && g.bites ? 'BIT — exposed a gap' : 'all passed'})`)
    }
  }

  // 2. DIAGNOSE — fix the worst gating check, or declare converged
  if (baseline && baseline.status === 'READY') {
    if (!grow) { log(`round ${i}: READY and grow disabled — converged`); break }
    log(`round ${i}: READY at the grown set — converged`); break
  }
  const wave = await newWave(P)
  const diag = await agent(
    `Diagnose benchmark '${bench}' (wave ${wave}). Current verdict: ${JSON.stringify(baseline)}.\n` +
    `Use .claude/skills/preact-bench/reference/optimization-surfaces.md and ` +
    `.claude/skills/optimize-verdict/reference/verdict-to-surface.md. Open the bench's --report HTML / probe ` +
    `trace, pick the single highest-value FAILING gating check, name its root cause and the SMALLEST surface ` +
    `to tune (weight → registry row → plugin → prompt/skill content → runtime). Write ` +
    `${BENCH_DIR}/improve/${wave}/diagnosis.md + rfc.md. If nothing gating is failing, return mode="converged". ` +
    `Return {mode, check, root_cause, surface}.`,
    { schema: DIAGNOSIS, label: 'diagnose', phase: P })
  if (!diag || diag.mode === 'converged') { log(`round ${i}: converged (${diag && diag.root_cause})`); break }

  // 3. IMPLEMENT — smallest legitimate fix, keep invariants green or revert
  const impl = await agent(
    `Implement the fix for '${bench}' wave ${wave} (from ${BENCH_DIR}/improve/${wave}/rfc.md): ` +
    `surface=${diag.surface}, target=${diag.check}. Apply the SMALLEST legitimate SDK change via that surface ` +
    `(never weaken a gate, branch the interpreter, stub a bench, or break the 5 invariants). Then run ` +
    `\`uv run python -m pytest -q\` + \`uv run ruff check agent_sdk && uv run ruff format agent_sdk\` ` +
    `(test_sdk_isolation, test_lobe_network parity, test_pinned_lobes_parity MUST stay green). If it can't ` +
    `stay green, REVERT (git restore/checkout) and return applied=false, reverted=true. Save ` +
    `\`git diff > ${BENCH_DIR}/improve/${wave}/diff.patch\`. Return {applied, gates_green, summary, reverted}.`,
    { schema: IMPLEMENT, label: 'implement', phase: P })
  if (!impl || !impl.applied || !impl.gates_green) {
    journal.push({ wave, kind: 'fix', kept: false, reason: 'not applied / gates red' })
    log(`round ${i}: fix not applied or gates red — skipped`)
    continue
  }

  // 4. RE-BENCH + RATCHET
  const after = await runBench(P, `${label}-${wave}`, `${BENCH_DIR}/improve/${wave}/after.json`)
  const decision = await agent(
    `Decide wave ${wave} of '${bench}'. Run the deterministic ratchet:\n` +
    `  ${CLI} promote ${BENCH_DIR} --wave ${wave} --after ${BENCH_DIR}/improve/${wave}/after.json ` +
    `--label ${label} ${model ? `--model ${model} ` : ''}--kind fix --note "${(diag.check || '').replace(/"/g, '')}"\n` +
    `It exits 0 if kept, 1 if reverted. If KEPT: \`git add -A && git commit\` subject ` +
    `\`fix(${bench}): ${diag.check || diag.surface} [${wave}]\` + the Co-Authored-By trailer (committed=true). ` +
    `If REVERTED: \`git restore .\` / \`git checkout -- .\` (committed=false). Return {kept, reason, committed}.`,
    { schema: DECISION, label: 'promote', phase: P })
  const kept = !!(decision && decision.kept)
  journal.push({ wave, kind: 'fix', check: diag.check, kept, reason: decision && decision.reason })
  if (kept) { baseline = after; log(`round ${i}: KEPT — ${decision.reason}`) }
  else log(`round ${i}: reverted — ${decision && decision.reason}`)
}

phase('Baseline')
log(`optimize-bench done · ${bench} · final ${JSON.stringify(baseline)}`)
return { bench, final: baseline, journal }
