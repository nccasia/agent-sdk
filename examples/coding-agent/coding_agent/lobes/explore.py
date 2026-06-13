"""explore lobe — read the relevant code before answering or editing."""

from __future__ import annotations

from agent_sdk import Layer, Lobe


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
