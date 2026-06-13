"""A coding agent built on agent_sdk — operates on a real filesystem workspace.

The whole capability is packaged as a first-class :class:`CodingPlugin`; ``build_coding_agent``
mounts it on a bare base network.
"""

from coding_agent.agent import CodingPlugin, build_coding_agent

__all__ = ["CodingPlugin", "build_coding_agent"]
