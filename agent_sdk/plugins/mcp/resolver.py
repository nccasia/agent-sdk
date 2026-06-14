"""Conditional capability resolver — pick which installations are active this turn.

A host may have several MCP servers (or other capability items) installed but want
only a subset live for a given turn — by channel, by deployment, or by a context
flag (a DM, an onboarding turn, …). This is the generic matching mechanism: each
item carries a declarative ``activation`` dict, and ``select_active`` keeps the
items whose activation matches an opaque per-turn ``context`` bag.

``activation`` keys (all optional; an absent/empty key never filters):
  - ``channel_ids``    — keep only when ``context["channel_id"]`` is one of them
  - ``deployment_ids`` — keep only when ``context["deployment_id"]`` is one of them
  - ``context_flags``  — keep only when EVERY flag passes ``flag_check(flag, context)``

The default ``flag_check`` is truthy ``context.get(flag)``; a host injects its own to
give a flag custom semantics (e.g. ``is_dm``) without that logic entering the leaf.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

__all__ = ["select_active", "activation_matches"]

FlagCheck = Callable[[str, dict], bool]


def _default_flag_check(flag: str, context: dict) -> bool:
    return bool(context.get(flag))


def activation_matches(
    activation: dict[str, Any],
    context: dict,
    *,
    flag_check: FlagCheck | None = None,
) -> bool:
    """True when one item's ``activation`` matches the per-turn ``context`` bag."""
    check = flag_check or _default_flag_check

    channel_ids = activation.get("channel_ids") or []
    if channel_ids and str(context.get("channel_id") or "") not in {str(v) for v in channel_ids}:
        return False

    deployment_ids = activation.get("deployment_ids") or []
    if deployment_ids and str(context.get("deployment_id") or "") not in {
        str(v) for v in deployment_ids
    }:
        return False

    return all(check(str(flag), context) for flag in activation.get("context_flags") or [])


def select_active(
    items: Sequence[Any],
    context: dict,
    *,
    flag_check: FlagCheck | None = None,
    activation_of: Callable[[Any], dict] | None = None,
) -> list[Any]:
    """Keep the items whose ``activation`` matches ``context``.

    ``activation_of`` extracts the activation dict from an item (default: its
    ``.activation`` attribute, else ``{}`` — an item with no activation is always
    active). Works on any item shape (an ``MCPServerSpec``, a host's installation
    dataclass, a plain dict).
    """
    get = activation_of or (lambda it: dict(getattr(it, "activation", None) or {}))
    return [it for it in items if activation_matches(get(it), context, flag_check=flag_check)]
