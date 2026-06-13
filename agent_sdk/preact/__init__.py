"""The built-in PreAct network — sensible default lobes / stages / flows.

The SDK framework ships no *required* concrete lobes, but a usable agent needs a
default reasoning network. ``Lobes.default()`` / ``Stages.default()`` /
``Flows.default()`` provide a small, real PreAct network (classify → plan →
research → synthesize, with the cite/filter output contract) so ``PreactAgent``
works out of the box; compose or replace any of them.
"""

from __future__ import annotations

from agent_sdk.preact.defaults import Flows, Lobes, Stages

__all__ = ["Lobes", "Stages", "Flows"]
