---
name: Sprint tracker
slug: sprint_tracker
description: Track the current sprint's tasks and notes across the conversation. Use when the user asks to add, list, or update sprint tasks, take standup notes, or check what is in progress. Keep the live task list and notes as the skill's workspace state.
stages: [synthesize]
required_tools: []
injection: on_demand
context_vars:
  - key: tasks
    type: todos
    title: "Sprint tasks"
    items:
      - title: "Review PR #482"
        status: doing
      - title: "Write migration 0047"
        status: todo
      - title: "Update the on-call rota"
        status: todo
  - key: notes
    type: notes
    title: "Standup notes"
    value: ""
---
SKILL: Sprint tracker

You maintain the sprint's live workspace. The current task list and standup notes are
pinned in your context as the authoritative state — recomputed every turn.

- When the user adds or updates a task, reflect it in the **Sprint tasks** list and persist
  it under `skill:sprint_tracker:tasks`.
- When the user gives a standup update, append it to **Standup notes** under
  `skill:sprint_tracker:notes`.
- When asked for status, read the pinned state rather than guessing.
