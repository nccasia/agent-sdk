# Method — <benchname>

> The optimization approach this bench drives. Fill every section; this is what makes the bench
> *improvable*, not just pass/fail. See `../_shared/TEMPLATE.md` for the standard.

## What it certifies
One sentence: the single SDK capability or layer this bench is the arbiter for.
(e.g. "Whether on-demand skills activate precisely and stay navigable under near-neighbor pressure.")

## The lever (optimization approach)
When this bench is NOT_READY, the fix is tuned **here** — name the surface(s) from
`../../.claude/skills/preact-bench/reference/optimization-surfaces.md`, smallest blast radius first:

| Failing dimension | Root-cause signal (from the probe trace) | Lever (surface to tune) |
|---|---|---|
| <e.g. precision low> | <e.g. fires on near-neighbor queries> | <e.g. `prior_<lobe>` / signal weight in `weights.py`> |
| <e.g. recall low>    | <e.g. misses a clear trigger>          | <e.g. registry edge / path bias>        |
| <e.g. content miss>  | <e.g. SOP lacks the mandate>           | <e.g. skill content>                    |

Hypothesis space (what a wave is allowed to change): <files / surfaces in scope>.
Out of scope (never touched to pass this bench): <e.g. the gating dataset, the interpreter>.

## Metrics & gates
Gating metrics decide the verdict; diagnostics are recorded but never gate (use for flappy signals).

| Metric | Direction | Gate (threshold) | Gating? |
|---|---|---|---|
| <precision> | higher | `>= 0.8` | gate |
| <recall>    | higher | `>= 0.8` | gate |
| <disclosure_ratio> | lower | (report) | diagnostic |

## Tiers & dataset
- **free** (deterministic, no provider): <what runs without an LLM — structure/lint/scoping checks>.
- **live** (`--live`, real provider, `--trials N` to pool variance): <what needs the model>.
- Dataset: `dataset/*.jsonl`, one scenario per line with an `expect` contract. Discriminating
  categories to include: near-neighbor, refusal/out-of-scope, adversarial, per-turn.

## READY means
<the exact bar: e.g. "every skill's gating checks pass and at least one live group measured it.">
