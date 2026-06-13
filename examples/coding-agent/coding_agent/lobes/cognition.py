"""Cognition lobes — the "think" disciplines of the coding agent.

``triage`` / ``explore`` are always-on (their slices appear in most stages);
``plan`` / ``implement`` / ``surveyor`` are lit by their flow's lobe bias so they
only contribute on the stages that consult them. Each lobe is a small,
self-describing context worker: metadata + a deterministic free activation + a
system-prompt contribution.
"""

from __future__ import annotations

from agent_sdk import Layer, Lobe


class Triage(Lobe):
    id = "triage"
    name = "Triage"
    description = "Classify the request: a question, a quick fix, or a feature."
    use_when = "every coding turn"
    layer = Layer.COGNITION
    behavior = "select"
    system_prompt = (
        "You are a careful senior software engineer working in a real repository. "
        "First understand exactly what is being asked: is it a question about the "
        "code, a small fix, or a multi-step change? Match your effort to the task."
    )

    def activation(self, ctx: dict) -> float:
        return 1.0


class Explore(Lobe):
    id = "explore"
    name = "Explore"
    description = "Read the relevant code before proposing or making changes."
    use_when = "before answering or editing"
    layer = Layer.COGNITION
    behavior = "gather"
    order = 1
    system_prompt = (
        "Before you answer or edit, ground yourself in the ACTUAL code — even in a "
        "large repo. Navigate efficiently: LS for layout, Glob (e.g. "
        "`**/*.py`, `apps/**/*.ts`) to find files by name, Grep to find symbols/"
        "usages, then Read (with offset/limit for big files) to read the exact "
        "lines. Follow imports and references to build a precise mental model. "
        "Never guess a file's contents — read it. Don't read the whole repo; reach "
        "the few files that matter."
    )

    def activation(self, ctx: dict) -> float:
        return 1.0


class Plan(Lobe):
    id = "plan"
    name = "Plan"
    description = "Decompose a multi-step change into concrete, ordered steps."
    use_when = "a feature or refactor that needs more than one edit"
    layer = Layer.COGNITION
    behavior = "decompose"
    order = 2
    system_prompt = (
        "Lay out the few concrete steps this change needs (which files, which "
        "functions, which tests). Keep it minimal — the smallest change that "
        "correctly does the job. Save the plan to memory (action=remember, "
        "scope=conversation, key=plan) so you can track progress across many steps "
        "without losing the thread."
    )

    def activation(self, ctx: dict) -> float:
        return 0.0  # lit by the feature flow's lobe bias


class Implement(Lobe):
    id = "implement"
    name = "Implement"
    description = "Write minimal, correct code that matches the surrounding style."
    use_when = "making the change"
    layer = Layer.COGNITION
    behavior = "compose"
    order = 3
    system_prompt = (
        "Make the change with Edit (exact string match — Read first so the match is "
        "exact) or Write for new files. Match the existing code's style, naming, and "
        "idioms. Change as little as possible. Add or update tests for what you "
        "changed. Do not leave the tree broken. As you complete plan steps, update "
        "the plan in memory."
    )

    def activation(self, ctx: dict) -> float:
        return 0.0  # lit by the feature/quick_fix flows' lobe bias


class Surveyor(Lobe):
    id = "surveyor"
    name = "Surveyor"
    description = "Map a large codebase's structure breadth-first before diving in."
    use_when = "understanding a whole system"
    layer = Layer.COGNITION
    behavior = "gather"
    order = 1
    system_prompt = (
        "Map the repository top-down before diving deep. Use LS on the root "
        "and key directories, Glob for the dominant file types and entry points "
        "(README, pyproject/package.json, __init__/main/index), and Grep for the "
        "high-level wiring. Build a mental table of contents — the subsystems, "
        "where each lives, and how they connect — so the plan can target them."
    )

    def activation(self, ctx: dict) -> float:
        return 0.0  # lit by the understand flow's lobe bias
