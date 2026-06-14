export const meta = {
  name: 'optimize-suite',
  description: 'Comprehensive full-suite optimizer: build a readiness matrix across the benches, then run the per-bench optimize-bench loop on each in priority order (grounding/correctness > routing > efficiency > coverage), and re-check already-green benches for cross-bench regression at the end. Drives the whole SDK upward, one bench at a time.',
  phases: [
    { title: 'Matrix' },
    { title: 'Optimize' },
    { title: 'Recheck' },
  ],
}

// args = { benches, rounds, model }
const ALL = ['flowbench', 'attentionbench', 'corgictionbech', 'toolbench', 'skillbench',
  'extensionbench', 'taskbench', 'agentbench', 'coding-agent-bench']
// priority: grounding/correctness first, then routing, then the heavier live benches
const PRIORITY = ['skillbench', 'toolbench', 'flowbench', 'attentionbench', 'corgictionbech',
  'extensionbench', 'taskbench', 'agentbench', 'coding-agent-bench']
const benches = (args && args.benches && args.benches.length) ? args.benches : ALL
const rounds = (args && args.rounds) || 3
const model = (args && args.model) || ''
const order = PRIORITY.filter((b) => benches.includes(b))

const MATRIX = {
  type: 'object', additionalProperties: true,
  properties: {
    rows: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: true,
        properties: { bench: { type: 'string' }, status: { type: 'string' }, failing: { type: 'number' } },
        required: ['bench', 'status'],
      },
    },
  },
  required: ['rows'],
}

phase('Matrix')
log(`optimize-suite · ${order.length} benches · rounds=${rounds}`)
const before = await agent(
  `From packages/agent-sdk, run \`bash benchmarks/ci-free-gates.sh\` (report GREEN/RED), then for each of ` +
  `[${order.join(', ')}] run its benchmark and read the \`verdict <STATUS>\` line (free benches: ` +
  `\`python benchmarks/<b>/run.py --report\`; live benches: add \`--live --report\`${model ? ` --model ${model}` : ''}). ` +
  `Return a readiness matrix {rows:[{bench,status,failing}]}. Run/report only — change nothing.`,
  { schema: MATRIX, label: 'matrix', phase: 'Matrix' })
log(`readiness: ${(before.rows || []).map((r) => `${r.bench}=${r.status}`).join(' · ')}`)

phase('Optimize')
const results = []
for (const bench of order) {
  const row = (before.rows || []).find((r) => r.bench === bench)
  if (row && row.status === 'READY') {
    // still run a round so the grow phase can expose the next gap
    log(`${bench}: READY — running one grow/converge round`)
    results.push(await workflow('optimize-bench', { bench, rounds: 1, model, grow: true }))
  } else {
    log(`${bench}: ${row ? row.status : '?'} — optimizing`)
    results.push(await workflow('optimize-bench', { bench, rounds, model, grow: true }))
  }
}

phase('Recheck')
const after = await agent(
  `From packages/agent-sdk, re-run every bench in [${order.join(', ')}] (free: \`run.py --report\`; live: ` +
  `\`--live --report\`${model ? ` --model ${model}` : ''}) and return the final readiness matrix ` +
  `{rows:[{bench,status,failing}]}. Flag any bench that REGRESSED versus the start (was READY, now not). ` +
  `Run/report only.`,
  { schema: MATRIX, label: 'recheck', phase: 'Recheck' })
log(`final readiness: ${(after.rows || []).map((r) => `${r.bench}=${r.status}`).join(' · ')}`)
return { before: before.rows, after: after.rows, results }
