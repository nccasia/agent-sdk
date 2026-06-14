# Scenario templates — the Grow phase playbook

How to **add more data scenarios** to each bench during the loop's Grow phase. Two goals every round:
**realistic** coverage (representative cases a production agent meets) and **adversarial** discrimination
(cases that expose the next real SDK gap). A scenario must be something a *correct* SDK should
satisfy — never an impossible/contradictory case just to force a red.

## Categories to author (mix both kinds each round)
- **realistic** — a representative in-domain query the bench doesn't cover yet.
- **near-neighbor** — a confusable case that pressures *precision* (two plausible targets; only one right).
- **refusal / out-of-scope** — asks for something not in scope; assert a grounded refusal, not a hallucination.
- **edge** — boundary values, empty/missing args, unusual phrasing.
- **per-turn** — a multi-turn case asserting *which* turn does what.

## Per-bench scenario surface

| Bench | Surface | How to add |
|---|---|---|
| **skillbench** | `dataset/scenarios.jsonl` (auto-loaded) | append a JSONL line |
| **toolbench** | `dataset/scenarios.jsonl` (live loop) | append a JSONL line; free spec/select/composite are inline in `run.py` |
| **taskbench** | `dataset/tasks.jsonl` | append a JSONL line (with a verifiable `answer_sql`) |
| **extensionbench** | `dataset/behaviors.jsonl` | append a JSONL line |
| **agentbench** | `dataset/*.jsonl` (generative mission) | prefer extending via its `gen_*` scripts; ad-hoc lines need mission wiring — grow cautiously |
| **flowbench** | inline `SCN` list in `run.py` | add a dict to `SCN` |
| **attentionbench** | inline `SCN` list in `run.py` | add a dict to `SCN` |
| **corgictionbech** | inline cases in `run.py` mode fns | add a constructed observation/snapshot case |
| **coding-agent-bench** | hardcoded task (not data-driven) | grow by adding a target repo / a new scored task — heavier; usually skip in Grow |

After adding, **re-run the bench** to see if the new cases *bite* (flip a gating check to FAIL). If they
all pass, the SDK already handles that — note it and reach for a harder case. Keep the free gate +
invariants green (a dataset edit must not break `pytest`).

## Schemas + examples

**skillbench** (`dataset/scenarios.jsonl`):
```json
{"id":"bp-near-01","category":"near_neighbor","query":"my enterprise plan was double-charged this month",
 "skills_under_test":["billing_policy","ticket_triage"],
 "expect_activation":{"billing_policy":true,"ticket_triage":false},
 "uplift":{"skill":"billing_policy","must_include":["prorated"],"must_not_include":["100%"]}}
{"id":"bp-refusal-01","category":"refusal","query":"can I pay my SaaS bill in Bitcoin?",
 "skills_under_test":["billing_policy"],"expect_activation":{"billing_policy":true},
 "uplift":{"skill":"billing_policy","must_include":["not"],"must_not_include":["yes, you can"]}}
```

**toolbench** (`dataset/scenarios.jsonl`, live loop):
```json
{"id":"loop-missing-arg-01","category":"edge","query":"look up an order's status (don't give the id)",
 "expect":{"status":"ok"}}
```

**taskbench** (`dataset/tasks.jsonl`):
```json
{"id":"orders-by-month","capability":6,"kind":"dynamic",
 "question":"How many orders were placed in each month of 2024, earliest first?",
 "answer_sql":"SELECT strftime('%m', created_at) m, COUNT(*) FROM orders WHERE created_at LIKE '2024-%' GROUP BY m ORDER BY m"}
```

**extensionbench** (`dataset/behaviors.jsonl`):
```json
{"id":"triage-near-01","kind":"plugin","query":"this is mildly annoying but not urgent — ticket 9 is slow",
 "triage_path":"triage","triage_tool":"lookup_ticket","triage_lobe":"triage",
 "note":"near-neighbor: should it still route to triage? assert the plugged behavior"}
```

**flowbench** (inline `SCN` — `{id, q, path, flow}`):
```python
{"id":"research-evidence","q":"summarize the differences between A and B with evidence and sources",
 "path":"research","flow":["research:plan","research:research","research:synthesize","research:cite","research:filter"]}
```
*(First inspect the real routing — `agent.inspect(q).path/.flow` — and author the expectation to match
the contract you intend; if the SDK routes it wrong, that's the gap the fix phase closes.)*

**attentionbench** (inline `SCN` — `{id, q, want:set, absent:set}`):
```python
{"id":"flood-refunds","q":"refund policy amid lots of unrelated chatter","want":{"synthesize","respond"},"absent":set()}
```

**corgictionbech** (inline, construct a `MetaObservation`/snapshot and assert the decision):
```python
# e.g. an empty pinned-step slice for a NEW pinned step must still escalate to meta_review, never skip
```

## Recording the growth
The workflow commits each Grow wave on its own (`test(<bench>): grow scenarios [wave]`) and records it
via `improve_cli promote … --kind dataset --scenarios-added N` (which re-baselines the bench). The
ratchet only goes up: never delete a discriminating scenario you added to make a verdict green — that's
the SDK-fix phase's job.
