<!--
Markdown report template for an agent-sdk benchmark run.

This is the canonical shape `render_report_md` (benchmarks/_shared/report.py) fills and writes to
`benchmarks/<bench>/verdicts/<bench>.md` on `--report` (a committed snapshot; the rich interactive
`<bench>.html` sits beside it). Placeholders in {curly braces} are substituted; the per-mode and
per-check blocks repeat. Keep it scannable: verdict first, then metrics, then each mode's checks,
then any probe traces.
-->

# {label} — benchmark report

> **Verdict: {status}** · {n_pass}/{n_total} checks · generated {generated_at}

{reasons_block}  <!-- "" when READY, else "Reasons: …" bullet list -->

## Metrics

{metrics_block}   <!-- "- key: value" per recorded metric; "_none_" if empty -->

## Modes

<!-- repeated per mode -->
### {mode} — {mode_pass}/{mode_total}

- {✓|✗} `{check_id}` — {detail}

## Probe traces

<!-- repeated per probed turn; section omitted entirely when there are no probes -->
### {probe_label} · {flow} · {probe_status}

- flow: {state → state → state}
- {n} tool calls
