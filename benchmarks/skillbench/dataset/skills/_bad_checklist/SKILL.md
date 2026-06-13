---
name: Broken checklist
slug: _bad_checklist
description: Walk the user through an onboarding checklist step by step. Use when the user says they are onboarding a new hire and wants a checklist.
stages: [synthesize]
required_tools: []
injection: on_demand
checklist:
  - key: step1
    required: true
  - key: step2
    required: true
    terminal: true
---
SKILL: Broken checklist

Work through the onboarding checklist one item at a time. (This fixture is deliberately
broken: its checklist steps carry neither a `title` nor an `ask`, so there is nothing to
present to the user — a materialized-checklist parse defect.)
