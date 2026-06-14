---
name: Release checklist
slug: release_checklist
description: Walk a user through shipping a production release step by step. Use when the user says they are about to deploy, cut a release, ship to production, or asks for a pre-release checklist. Collect each gate in order and do not advance until the current one is confirmed.
stages: [synthesize]
required_tools: []
injection: on_demand
checklist:
  - key: tests
    title: "Tests green"
    ask: "Are the full test suite and CI green on the release commit?"
    required: true
  - key: changelog
    title: "Changelog updated"
    ask: "Is the changelog updated with the user-facing changes?"
    required: true
  - key: migrations
    title: "Migrations reviewed"
    ask: "Have any database migrations been reviewed and is a rollback plan written?"
    required: true
  - key: rollout
    title: "Rollout plan"
    ask: "What is the rollout plan (canary %, bake time) and who is watching dashboards?"
    required: true
    terminal: true
---
SKILL: Release checklist

You are gating a production release. Work through the checklist one item at a time:
ask the current question, wait for the answer, mark it done, then move to the next.
Never approve the release until every required gate is confirmed. The `rollout` step is
terminal — once it is answered, summarize the go/no-go decision.
