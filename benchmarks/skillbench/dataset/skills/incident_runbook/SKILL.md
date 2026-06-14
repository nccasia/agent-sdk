---
name: Incident runbook
slug: incident_runbook
description: Drive the on-call incident response procedure. Use when the user reports a production outage, a sev1/sev2 incident, an alert firing, or says a service is down and asks what to do. This runbook is eager — its steps are always in force during an incident turn.
stages: [synthesize]
required_tools: []
injection: eager
---
SKILL: Incident runbook

Run the incident response procedure in order. Do not skip a step.

1. ACKNOWLEDGE — state the incident, its severity, and the affected service in one line.
2. STABILIZE — propose the fastest safe mitigation (roll back, fail over, or drain) before
   any root-cause work.
3. COMMUNICATE — post a status line for stakeholders: what is impacted, since when, and
   the next update time.
4. DIAGNOSE — only after stabilizing, investigate the root cause.
5. RECORD — capture a timeline as you go so the postmortem writes itself.

Always lead with mitigation, never with diagnosis.
