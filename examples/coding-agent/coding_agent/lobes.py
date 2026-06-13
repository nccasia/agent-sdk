"""Production-shaped coding lobes (the OY context axis).

Each lobe is a small, self-describing context worker: metadata + a deterministic
free activation + a system-prompt contribution. These encode a coding agent's
disciplines — explore before editing, plan multi-step work, write code that
matches surrounding style, verify with the real test suite, report honestly.

``triage`` / ``explore`` / ``summarize`` are always-on (their slices appear in
most stages); ``plan`` / ``implement`` / ``verify`` are lit by their flow's lobe
bias so they only contribute on the stages that consult them.
"""

from __future__ import annotations

from agent_sdk import Lobe, Layer


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


class Verify(Lobe):
    id = "verify"
    name = "Verify"
    description = "Run the real test suite / build and report the result honestly."
    use_when = "after making a change"
    layer = Layer.EXPRESSION
    behavior = "verify"
    order = 8
    system_prompt = (
        "Run the project's tests (or the most relevant subset) with Bash and "
        "read the output. If anything fails, fix it. Report pass/fail truthfully — "
        "never claim success you did not observe."
    )

    def activation(self, ctx: dict) -> float:
        return 0.0  # lit by the feature/quick_fix flows' lobe bias


class Summarize(Lobe):
    id = "summarize"
    name = "Summarize"
    description = "State concisely what changed (files touched) and the test result."
    use_when = "producing the final reply"
    layer = Layer.EXPRESSION
    behavior = "compose"
    order = 9
    system_prompt = (
        "Summarize for a reviewer: what you changed, which files, and the test "
        "result. Be concrete and brief. If you could not complete the task, say so "
        "plainly and explain what is blocking."
    )

    def activation(self, ctx: dict) -> float:
        return 1.0


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


class Documenter(Lobe):
    id = "documenter"
    name = "Documenter"
    description = "Aggregate findings into a clear architecture document."
    use_when = "writing the architecture overview"
    layer = Layer.EXPRESSION
    behavior = "compose"
    order = 9
    system_prompt = (
        "Recall ALL findings you saved to memory (action=recall, scope=conversation, "
        "with a query like 'finding') and synthesize them into a single, well-"
        "structured Markdown architecture document. Then WRITE it to disk in ONE "
        "call: either Write(file_path='ARCHITECTURE.md', content=<the FULL document "
        "text>) — always include the complete `content` — or a `cat > ARCHITECTURE.md "
        "<<'EOF' … EOF` heredoc via Bash. The document must include: a one-paragraph "
        "overview, a subsystem-by-subsystem breakdown (what each does + its key "
        "files), how the pieces fit together (the data/control flow), and the main "
        "entry points. Cite concrete file paths. Be accurate — only state what you "
        "actually read; do not invent APIs or line numbers."
    )

    def activation(self, ctx: dict) -> float:
        return 0.0  # lit by the understand flow's lobe bias


def coding_lobes() -> list[Lobe]:
    return [Triage(), Explore(), Plan(), Implement(), Verify(), Summarize(),
            Surveyor(), Documenter()]
