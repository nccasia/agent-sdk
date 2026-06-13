"""Re-export of the ported deterministic building blocks.

This module gathers the framework primitives that were lifted (mechanically)
from ``agent_core.sdk`` — contracts, the deterministic activation network, the
lobe/flow frameworks + registries + ``tool_loop`` runtime, metacognition, the
ReAct funnel, skills, and inspection. The top-level :mod:`agent_sdk` package
re-exports these alongside the new PreAct façade (``PreactAgent``, ``Stage``,
``@tool``, clients, stores, plugins, serving).
"""

from __future__ import annotations

# ── contracts (the dependency-free base) ────────────────────────────────────
from agent_sdk.contracts.llm import LlmCall
from agent_sdk.contracts.memo import (
    Citation,
    Claim,
    FinalEnvelope,
    Memo,
    strip_memory_footer,
)
from agent_sdk.contracts.pins import PINNED_LOBES
from agent_sdk.contracts.services import LobeServices
from agent_sdk.contracts.tools import CompositeToolRuntime, ToolRuntime
from agent_sdk.contracts.turn import (
    LobeResult,
    PromptContribution,
    StageResult,
    TurnContext,
)

# ── activation network (pure, deterministic) ────────────────────────────────
from agent_sdk.flows.flow import (
    BaseFlow,
    Flow,
    FlowStep,
    FlowStepNode,
    FlowStepResult,
)
from agent_sdk.flows.registry import FlowRegistry, set_default_flows
from agent_sdk.inspection import (
    AxisOptimization,
    EngineSnapshot,
    FlowAxisSnapshot,
    LobeAxisSnapshot,
    inspect_flow_axis,
    inspect_lobe_axis,
    snapshot_engine,
    suggest_axis_optimizations,
)
from agent_sdk.lobes.registry import LobeRegistry, set_default_providers
from agent_sdk.lobes.runtime import (
    BaseLobe,
    Lobe,
    datetime_block,
    extract_text,
    tool_loop,
)
from agent_sdk.metacognition import (
    MetaController,
    MetaDecision,
    MetaObservation,
    monitor,
    regulate,
)
from agent_sdk.network.activation import (
    LAYER_COGNITION,
    LAYER_EXPRESSION,
    LAYER_INSTINCT,
    LAYER_MEMORY,
    LAYER_PERCEPTION,
    LAYER_SKILL,
    Blackboard,
    ContextBound,
    LobeNode,
    LobeSpec,
    NetworkResolution,
    PathSpec,
    merge_lobe_weights,
    propagate,
    propagate_nodes,
    recognize_paths,
    resolve_path,
    validate_network,
)
from agent_sdk.network.context_builder import ContextNode, build_attention
from agent_sdk.react.funnel import compact_observations, tier_observations
from agent_sdk.skills import SkillPack, SkillRegistry, build_skill_prompt_block

__all__ = [
    # contracts
    "LlmCall",
    "LobeServices",
    "TurnContext",
    "PromptContribution",
    "LobeResult",
    "StageResult",
    "ToolRuntime",
    "CompositeToolRuntime",
    "Citation",
    "Claim",
    "Memo",
    "FinalEnvelope",
    "strip_memory_footer",
    "PINNED_LOBES",
    # network
    "LobeSpec",
    "PathSpec",
    "LobeNode",
    "ContextBound",
    "Blackboard",
    "NetworkResolution",
    "ContextNode",
    "build_attention",
    "propagate",
    "propagate_nodes",
    "recognize_paths",
    "resolve_path",
    "validate_network",
    "merge_lobe_weights",
    "LAYER_INSTINCT",
    "LAYER_PERCEPTION",
    "LAYER_MEMORY",
    "LAYER_SKILL",
    "LAYER_COGNITION",
    "LAYER_EXPRESSION",
    # lobe framework
    "BaseLobe",
    "Lobe",
    "LobeRegistry",
    "set_default_providers",
    "tool_loop",
    "extract_text",
    "datetime_block",
    # flow framework
    "Flow",
    "FlowStep",
    "FlowStepNode",
    "FlowStepResult",
    "BaseFlow",
    "FlowRegistry",
    "set_default_flows",
    # metacognition
    "MetaController",
    "MetaDecision",
    "MetaObservation",
    "monitor",
    "regulate",
    # react + skills
    "tier_observations",
    "compact_observations",
    "SkillRegistry",
    "SkillPack",
    "build_skill_prompt_block",
    # inspection
    "inspect_lobe_axis",
    "inspect_flow_axis",
    "snapshot_engine",
    "suggest_axis_optimizations",
    "LobeAxisSnapshot",
    "FlowAxisSnapshot",
    "EngineSnapshot",
    "AxisOptimization",
]
