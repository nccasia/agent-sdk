"""Expression lobes — the "deliver" disciplines of the coding agent.

``summarize`` is always-on (the final reply of most flows); ``verify`` and
``documenter`` are lit by their flow's lobe bias. These encode honest delivery:
run the real tests and report truthfully, and write an architecture doc grounded
only in code that was actually read.
"""

from __future__ import annotations

from agent_sdk import Layer, Lobe


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
