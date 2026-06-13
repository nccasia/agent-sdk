"""agent_sdk — PreAct: a portable, pre-structured-acting agent SDK.

PreAct is *pre-structured acting*: the agent doesn't free-act by letting the model
pick tool calls turn by turn (vanilla ReAct) — its acting is shaped by a
deliberate thinking model (layered **lobes** → reusable **stages** → intent
**flows**), with metacognition supervising. See ``docs/preact.md`` and
``docs/api.md``.

    from agent_sdk import PreactAgent, tool
    from agent_sdk.clients import AnthropicClient

    @tool
    async def search(query: str, top_k: int = 5) -> str:
        "Search the knowledge base."

    agent = PreactAgent(client=AnthropicClient("claude-opus-4-6"),
                        instructions="You are a helpful research assistant.",
                        tools=[search])
    result = await agent.query("What changed in v2?")
"""

from __future__ import annotations

# ── ported deterministic building blocks (framework primitives) ──────────────
from agent_sdk._blocks import (  # noqa: E402
    PINNED_LOBES,
    Blackboard,
    Citation,
    Claim,
    CompositeToolRuntime,
    ContextNode,
    FinalEnvelope,
    LlmCall,
    Lobe,
    LobeRegistry,
    LobeServices,
    LobeSpec,
    Memo,
    PathSpec,
    SkillPack,
    SkillRegistry,
    ToolRuntime,
    TurnContext,
    build_attention,
    build_skill_prompt_block,
    propagate,
    recognize_paths,
    resolve_path,
    tool_loop,
    validate_network,
)

# ── façade: the public PreAct surface ────────────────────────────────────────
from agent_sdk.activable import Activable, Layer
from agent_sdk.agent import PreactAgent
from agent_sdk.bench import Harness, Report, Scenario, ScenarioResult
from agent_sdk.engine import Engine
from agent_sdk.events import (
    AgentStream,
    CitationFound,
    Final,
    MetaAction,
    PathResolved,
    RunStart,
    StageEnd,
    StageStart,
    TextDelta,
    ToolCall,
    ToolResult,
)
from agent_sdk.flow_def import Flow, flow
from agent_sdk.mcp import MCPError, MCPServerSpec, MCPToolRuntime
from agent_sdk.memory import Memory, MemoryItem, Scratchpad
from agent_sdk.metacognition_facade import Metacognition
from agent_sdk.preact.defaults import Flows, Lobes, Stages
from agent_sdk.probe import ProbeRecord, probe
from agent_sdk.react.docguard import DocWriteGuard
from agent_sdk.react.grounding import DocGroundingGuard
from agent_sdk.report import render_html, write_html
from agent_sdk.result import (
    ActivationSnapshot,
    AgentResult,
    MemoryUpdate,
    Optimization,
    Refusal,
    Trace,
    Usage,
)
from agent_sdk.session import Session, SessionState, Turn
from agent_sdk.signals import compile_signal, eval_signal
from agent_sdk.skill_def import Skill
from agent_sdk.stages import Stage, StageRegistry, stage
from agent_sdk.tools import FunctionToolRuntime, Tool, tool
from agent_sdk.viewer import render_viewer_html, to_viewer_record, write_viewer

__all__ = [
    # façade
    "PreactAgent",
    "Engine",
    "Activable",
    "Layer",
    "Stage",
    "StageRegistry",
    "stage",
    "Flow",
    "flow",
    "Flows",
    "Lobes",
    "Stages",
    "Skill",
    "Session",
    "SessionState",
    "Turn",
    "Memory",
    "MemoryItem",
    "Scratchpad",
    "Metacognition",
    "tool",
    "Tool",
    "FunctionToolRuntime",
    "MCPToolRuntime",
    "MCPServerSpec",
    "MCPError",
    "compile_signal",
    "eval_signal",
    # benchmark + probe + report
    "Harness",
    "Scenario",
    "ScenarioResult",
    "Report",
    "probe",
    "ProbeRecord",
    "DocWriteGuard",
    "DocGroundingGuard",
    "render_html",
    "write_html",
    "render_viewer_html",
    "write_viewer",
    "to_viewer_record",
    # results + events
    "AgentResult",
    "AgentStream",
    "Trace",
    "Usage",
    "Refusal",
    "MemoryUpdate",
    "Optimization",
    "ActivationSnapshot",
    "Citation",
    "RunStart",
    "PathResolved",
    "StageStart",
    "TextDelta",
    "ToolCall",
    "ToolResult",
    "CitationFound",
    "MetaAction",
    "StageEnd",
    "Final",
    # framework primitives
    "Lobe",
    "LobeSpec",
    "LobeRegistry",
    "PathSpec",
    "Blackboard",
    "ContextNode",
    "build_attention",
    "propagate",
    "recognize_paths",
    "resolve_path",
    "validate_network",
    "tool_loop",
    "LlmCall",
    "LobeServices",
    "TurnContext",
    "ToolRuntime",
    "CompositeToolRuntime",
    "SkillRegistry",
    "SkillPack",
    "build_skill_prompt_block",
    "Claim",
    "Memo",
    "FinalEnvelope",
    "PINNED_LOBES",
]
