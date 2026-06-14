---
name: Ticket triage
slug: ticket_triage
description: Classify and route an incoming support ticket. Use when the user pastes a customer support ticket, a bug report, or a help-desk message and asks how to categorize, prioritize, or route it. Do not use this for reviewing source code — that is the code review skill.
stages: [synthesize]
required_tools: []
injection: on_demand
---
SKILL: Ticket triage

When triaging a support ticket:

1. Classify the type: bug, feature request, billing, or how-to question.
2. Set a priority: P1 (outage / data loss), P2 (broken feature, has workaround), P3 (minor
   or cosmetic), P4 (question).
3. Route to the owning team based on the type.
4. Draft a one-line acknowledgement the agent can send back to the customer.

Decide from the ticket text alone; never ask the customer to re-explain.
