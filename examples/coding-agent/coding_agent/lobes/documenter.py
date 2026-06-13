"""documenter lobe — aggregate findings into a clear architecture document."""

from __future__ import annotations

from agent_sdk import Layer, Lobe


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
