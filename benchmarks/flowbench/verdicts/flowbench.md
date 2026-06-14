# flowbench · flow axis (OX) — benchmark report

> **Verdict: READY** · 63/63 checks · generated n/a

## Metrics

- `tiers.tiers`: ['deep', 'direct', 'standard', 'steward']
- `coverage.flows_defined`: 6
- `coverage.flows_covered`: 6

## Modes

### routing — 11/11

- ✓ `routing.relational-hi` — path=relational flow=['relational:synthesize']
- ✓ `routing.relational-thanks` — path=relational flow=['relational:synthesize']
- ✓ `routing.qna-fact` — path=qna flow=['qna:act']
- ✓ `routing.qna-howto` — path=qna flow=['qna:act']
- ✓ `routing.clarify-followup` — path=clarify flow=['clarify:synthesize']
- ✓ `routing.research-compare` — path=research flow=['research:act', 'research:cite', 'research:filter']
- ✓ `routing.research-tradeoffs` — path=research flow=['research:act', 'research:cite', 'research:filter']
- ✓ `routing.fallback-nonsense` — path=emergent flow=['fallback:act']
- ✓ `routing.onboarding-steward` — path=onboarding flow=['onboarding:synthesize']
- ✓ `routing.adv-greeting-question` — path=qna flow=['qna:act']
- ✓ `routing.adv-imperative` — path=emergent flow=['fallback:act']

### tiers — 12/12

- ✓ `tiers.relational-hi` — tier=direct grounded=False
- ✓ `tiers.relational-thanks` — tier=direct grounded=False
- ✓ `tiers.qna-fact` — tier=standard grounded=False
- ✓ `tiers.qna-howto` — tier=standard grounded=False
- ✓ `tiers.clarify-followup` — tier=standard grounded=False
- ✓ `tiers.research-compare` — tier=deep grounded=True
- ✓ `tiers.research-tradeoffs` — tier=deep grounded=True
- ✓ `tiers.fallback-nonsense` — tier=standard grounded=False
- ✓ `tiers.onboarding-steward` — tier=steward grounded=False
- ✓ `tiers.adv-greeting-question` — tier=standard grounded=False
- ✓ `tiers.adv-imperative` — tier=standard grounded=False
- ✓ `tiers.spectrum_covered` — covered=['deep', 'direct', 'standard', 'steward']

### states — 11/11

- ✓ `states.relational-hi` — states=['synthesize'] vocab=True ordered=True
- ✓ `states.relational-thanks` — states=['synthesize'] vocab=True ordered=True
- ✓ `states.qna-fact` — states=['act'] vocab=True ordered=True
- ✓ `states.qna-howto` — states=['act'] vocab=True ordered=True
- ✓ `states.clarify-followup` — states=['synthesize'] vocab=True ordered=True
- ✓ `states.research-compare` — states=['act', 'cite', 'filter'] vocab=True ordered=True
- ✓ `states.research-tradeoffs` — states=['act', 'cite', 'filter'] vocab=True ordered=True
- ✓ `states.fallback-nonsense` — states=['act'] vocab=True ordered=True
- ✓ `states.onboarding-steward` — states=['synthesize'] vocab=True ordered=True
- ✓ `states.adv-greeting-question` — states=['act'] vocab=True ordered=True
- ✓ `states.adv-imperative` — states=['act'] vocab=True ordered=True

### grounding — 11/11

- ✓ `grounding.relational-hi` — grounded=False tail=['synthesize']
- ✓ `grounding.relational-thanks` — grounded=False tail=['synthesize']
- ✓ `grounding.qna-fact` — grounded=False tail=['act']
- ✓ `grounding.qna-howto` — grounded=False tail=['act']
- ✓ `grounding.clarify-followup` — grounded=False tail=['synthesize']
- ✓ `grounding.research-compare` — grounded=True tail=['cite', 'filter']
- ✓ `grounding.research-tradeoffs` — grounded=True tail=['cite', 'filter']
- ✓ `grounding.fallback-nonsense` — grounded=False tail=['act']
- ✓ `grounding.onboarding-steward` — grounded=False tail=['synthesize']
- ✓ `grounding.adv-greeting-question` — grounded=False tail=['act']
- ✓ `grounding.adv-imperative` — grounded=False tail=['act']

### coverage — 1/1

- ✓ `coverage.all_flows_tested` — defined=['clarify', 'fallback', 'onboarding', 'qna', 'relational', 'research'] untested=[]

### determinism — 4/4

- ✓ `determinism.relational-hi` — identical across two inspects
- ✓ `determinism.relational-thanks` — identical across two inspects
- ✓ `determinism.qna-fact` — identical across two inspects
- ✓ `determinism.qna-howto` — identical across two inspects

### subject — 2/2

- ✓ `subject.threaded` — subject text in prompt
- ✓ `subject.tagged` — subject rendered as its own <subject> section

### execution — 11/11

- ✓ `execution.relational-hi` — ran=['relational:synthesize'] status=answered
- ✓ `execution.relational-thanks` — ran=['relational:synthesize'] status=answered
- ✓ `execution.qna-fact` — ran=['qna:act'] status=answered
- ✓ `execution.qna-howto` — ran=['qna:act'] status=answered
- ✓ `execution.clarify-followup` — ran=['clarify:synthesize'] status=answered
- ✓ `execution.research-compare` — ran=['research:act', 'research:cite', 'research:filter'] status=answered
- ✓ `execution.research-tradeoffs` — ran=['research:act', 'research:cite', 'research:filter'] status=answered
- ✓ `execution.fallback-nonsense` — ran=['fallback:act'] status=answered
- ✓ `execution.onboarding-steward` — ran=['onboarding:synthesize'] status=answered
- ✓ `execution.adv-greeting-question` — ran=['qna:act'] status=answered
- ✓ `execution.adv-imperative` — ran=['fallback:act'] status=answered
