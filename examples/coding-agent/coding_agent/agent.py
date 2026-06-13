"""Assemble a Claude-Code-grade coding agent: lobes → stages → flows → tools.

Built for *real, large* repositories and *long* runs (hundreds of tool calls):

- **Claude Code's canonical tools** (``Read``/``Write``/``Edit``/``Bash``/``Glob``/
  ``Grep``/``LS``, with the same param shapes — ``file_path``/``old_string``/…) so the
  model leans on its training priors: higher accuracy, fewer instruction tokens.
- **PreAct** (``funnel=True``) so spent tool observations shrink to hints —
  the agent can explore for a hundred hops without overflowing the window.
- **High per-stage hop budgets** so a deep question or feature isn't cut short.
- **Durable memory** for tracking the plan/goals across turns (the ``memory``
  tool, conversation scope).

The pieces live in focused subpackages — :mod:`coding_agent.lobes` (context
disciplines), :mod:`coding_agent.flows` (intents + stages),
:mod:`coding_agent.tools` (the workspace toolset) — and this module wires them
into one installable :class:`CodingPlugin`.
"""

from __future__ import annotations

import os
from typing import Any

from agent_sdk import DocGroundingGuard, DocWriteGuard, Memory, PreactAgent

from coding_agent.flows import READONLY_STAGES, coding_flows, coding_stages
from coding_agent.lobes import coding_lobes
from coding_agent.repomap import build_repo_map
from coding_agent.tools import coding_tools

# Terse on purpose: the tools are Claude Code's canonical Read/Write/Edit/Bash/Glob/
# Grep/LS, so the model already knows their semantics from training — we state the
# workflow, not the tool mechanics, which keeps the token budget low and accuracy high.
INSTRUCTIONS = (
    "You are an interactive coding agent working in the user's real repository "
    "(which may be large). Follow standard practice:\n"
    "- Orient before acting: use Glob and Grep to locate the few relevant files, then "
    "Read the exact files (offset/limit for large ones). Never guess file contents.\n"
    "- Edit with Edit (exact-string match — Read first) or Write for new files. Match "
    "the surrounding style, change as little as possible, and keep the tree green — run "
    "the tests with Bash.\n"
    "- Track multi-step work in memory (action=remember, scope=conversation, key=plan).\n"
    "- Report concisely what you found or changed, citing concrete files.\n"
    "\n"
    "To understand a whole system and document it: survey the structure (Glob/Grep/LS), "
    "plan the subsystems, investigate each by Reading the real code, saving each finding "
    "to memory (key=finding:<area>); then recall all findings and Write a single "
    "ARCHITECTURE.md."
)


class CodingPlugin:
    """The whole coding capability packaged as ONE first-class plugin.

    It contributes the full capacity surface — lobes + stages + flows + tools + the
    read-only write guard — through the SDK plugin seam, so the entire agent ships as a
    single installable unit:

        PreactAgent(client=…, plugins=[CodingPlugin(root)], lobes=[], stages=[], flows=[])

    (which is exactly what :func:`build_coding_agent` does — a bare base network the plugin
    fills in). Compose it alongside other plugins, or have it own an MCP server via the
    ``mcp_servers`` attribute / ``setup.add_mcp_server``.
    """

    name = "coding"

    def __init__(self, root: str):
        self.root = root

    def install(self, setup: Any) -> None:
        for lobe in coding_lobes():
            setup.add_lobe(lobe)
        for st in coding_stages():
            setup.add_stage(st)
        for fl in coding_flows():
            setup.add_flow(fl)
        for t in coding_tools(self.root):
            setup.add_tool(t)
        # Read-only stages must not write files (see flows.stages.READONLY_STAGES).
        setup.add_tool_filter(DocWriteGuard(
            write_tools=("Write",), bash_tool="Bash", readonly_stages=READONLY_STAGES,
        ))
        # A written doc must not cite paths that don't exist (the coding analog of
        # "refuse ungrounded claims"). exists() is resolved against the real root.
        _root = os.path.abspath(self.root)
        setup.add_tool_filter(DocGroundingGuard(
            exists=lambda p: os.path.exists(os.path.join(_root, p)),
            read_tools=("Read",), write_tools=("Write",), doc_suffixes=(".md",),
        ))


def build_coding_agent(
    root: str, *, client: Any, share_history: bool = True, memory: Memory | None = None,
    **kwargs: Any,
) -> PreactAgent:
    """Build a Claude-Code-grade coding agent bound to the real directory ``root``.

    The agent is assembled by mounting :class:`CodingPlugin` — the whole coding capability as
    one plugin — on a bare base network (``lobes=[]``/``stages=[]``/``flows=[]``). This is the
    SDK's first-class plug-and-play model: a capability is an installable unit, not a pile of
    constructor lists. Pass extra ``plugins=[…]`` or ``mcp_servers=[…]`` to augment it.

    ``client`` is any SDK client (``AnthropicClient(...)`` live, or ``FakeClient`` for
    deterministic demos/tests). ``share_history`` (default on) keeps the agent's
    exploration/edits visible to later stages; ``funnel`` keeps the context bounded across
    hundreds of tool calls. A ``Memory`` is attached so the agent can track its plan/goals.

    The ``budgets`` opt into the value-aware working-set discipline: spent observations beyond
    ``working_set_keep`` highest-CDS ones (scored vs the stage goal) demote to hints, and the
    observation tail compacts once it exceeds ``working_set_budget`` tokens — so context
    plateaus instead of growing O(hops) over a long exploration.
    """
    budgets = {
        "working_set_budget": 6000,   # tokens of live observation tail before compaction
        "working_set_keep": 4,        # highest-CDS observations pinned full by goal
        "working_set_max_spent": 8,   # recent spent-hint pairs kept before folding
        "stall_patience": 3,          # consecutive no-progress hops before forcing a final answer
        "enforce_tool_allowlist": True,  # a read-only stage structurally cannot write (not just hidden)
        **kwargs.pop("budgets", {}),
    }
    extra_plugins = list(kwargs.pop("plugins", []) or [])
    # Inject the deterministic repo map so the agent orients on the REAL structure
    # up front (free, cached in the prompt prefix) instead of rediscovering it with
    # dozens of LS/Glob/Read hops and guessing paths that don't exist.
    instructions = INSTRUCTIONS
    if kwargs.pop("repo_map", True):
        instructions = f"{INSTRUCTIONS}\n\n{build_repo_map(root)}"
    return PreactAgent(
        client=client,
        instructions=instructions,
        plugins=[CodingPlugin(root), *extra_plugins],
        lobes=[],              # the plugin provides the whole network
        stages=[],
        flows=[],
        memory=memory if memory is not None else Memory(),
        share_history=share_history,
        funnel=True,           # hundreds of tool calls in a bounded window
        tools_in_prompt=True,  # show the tool menu inline in each stage prompt
        budgets=budgets,
        **kwargs,
    )
