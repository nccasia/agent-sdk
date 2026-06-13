"""The coding agent's stages — the progressive-execution (OX) axis.

A stage is a bounded unit of work with its own lobe slice, tool allowlist, loop
mode, and hop budget. The flows (intents) in :mod:`coding_agent.flows` sequence
these stages. High per-stage hop budgets let a deep question or feature run long
without being cut short; the stall-break ends an agentic stage early once
exploration stops making progress.
"""

from __future__ import annotations

from agent_sdk import stage

# Read-only stages must not write files — block a `bash` heredoc (`cat > FILE`)
# from smuggling a write past the per-stage tool allowlist, and steer a repeated
# full rewrite of the same file (within a stage) toward an edit. Fixes the live
# symptom of the architecture doc written 3× during the *survey* stage. Consumed
# by the DocWriteGuard the CodingPlugin installs.
READONLY_STAGES = ("explore", "survey", "investigate", "answer", "plan")

# Tool slices per stage (Claude Code's canonical tool names).
_READ_TOOLS = ["LS", "Glob", "Grep", "Read", "Bash"]
_EDIT_TOOLS = ["Read", "Write", "Edit", "Glob", "Grep", "Bash"]
_VERIFY_TOOLS = ["Bash", "Read", "Edit", "Grep"]
_NOTE_TOOLS = ["LS", "Glob", "Grep", "Read", "memory", "Bash"]
# `Bash` is included so the model can write the doc via its preferred method
# (e.g. a `cat > FILE` heredoc) as a REAL tool call instead of leaking markup
# when only Write is offered.
_DOC_TOOLS = ["memory", "Read", "Glob", "Write", "Bash"]


def coding_stages() -> list:
    return [
        stage(
            "explore", lobes=["triage", "explore"], loop="agentic", tools=_READ_TOOLS,
            description="Navigate + read the codebase to ground the work.", hops=50,
        ),
        stage(
            "plan", lobes=["plan"], loop="single",
            description="Decompose a multi-step change into ordered steps.",
        ),
        stage(
            "implement", lobes=["implement"], loop="agentic", tools=_EDIT_TOOLS,
            description="Make the change on disk.", hops=80,
        ),
        stage(
            "verify", lobes=["verify"], loop="agentic", tools=_VERIFY_TOOLS,
            description="Run the tests and fix failures.", hops=40,
        ),
        stage(
            "answer", lobes=["triage", "explore", "summarize"], loop="agentic",
            tools=_READ_TOOLS,
            description="Deeply explore, then answer a question about the code.",
            hops=80,  # stall-break ends early when exploration stops making progress
        ),
        stage(
            "summarize", lobes=["summarize"], loop="single",
            description="Report what changed (files + test result).",
        ),
        # ── codebase-understanding pipeline ──────────────────────────────────
        stage(
            "survey", lobes=["triage", "surveyor"], loop="agentic", tools=_READ_TOOLS,
            description="Map the repository structure top-down.", hops=40,
        ),
        stage(
            "investigate", lobes=["explore"], loop="agentic", tools=_NOTE_TOOLS,
            description="Follow the plan: read each subsystem, save findings to memory.",
            hops=80,  # stall-break ends early when exploration stops making progress
        ),
        stage(
            "document", lobes=["documenter"], loop="agentic", tools=_DOC_TOOLS,
            description="Aggregate findings + write the architecture document.", hops=50,
            max_tokens=8000,  # the architecture doc is large — fit it in one call
        ),
    ]
