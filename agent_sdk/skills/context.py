"""Skill context — the live workspace state a skill carries while active.

A skill's ``context_vars`` (checklist / todos / notes / var) are its durable
working memory. These renderers turn them into the authoritative pinned block the
model sees — surfaced by the ``skill_active`` lobe (next-turn) and the
``ActivateSkill`` tool result (the on-demand commitment moment), so the model sees
the same live state wherever it lands.
"""

from __future__ import annotations

from typing import Any


def render_context_var(skill_id: str, var: dict) -> str:
    """Render one context var as the authoritative live workspace block.

    Shared by the ``skill_active`` lobe (eager / next-turn surfacing) and the
    ``skill.read`` tool result (on_demand commitment moment), so the model sees
    the SAME live state wherever it lands. ``checklist``/``todos`` render as a
    numbered status list; other types as a ``title: value`` line."""
    key = str(var.get("key") or var.get("title") or "var")
    title = str(var.get("title") or key)
    vtype = str(var.get("type") or "var")
    if vtype in ("checklist", "todos"):
        lines = [f"### Skill {skill_id} · {title}"]
        for i, it in enumerate(var.get("items") or []):
            if isinstance(it, dict):
                label = it.get("title") or it.get("ask") or it.get("key") or "item"
                status = it.get("status") or "todo"
            else:
                label, status = str(it), "todo"
            lines.append(f"  {i + 1}. [{status}] {label}")
        lines.append(
            f"Advance the next open item, then persist progress under "
            f"`skill:{skill_id}:{key}` via todos.update / memory."
        )
        return "\n".join(lines)
    val = var.get("value")
    body = (
        f"{title}: {val}"
        if val
        else (f"{title} (empty) — track it under `skill:{skill_id}:{key}` via the memory tool")
    )
    return f"### Skill {skill_id} · {body}"


def render_context_vars_block(pack: Any) -> str:
    """The full pinned context-vars block for a skill pack, or ``""`` if it
    declares none. One ``render_context_var`` per var under a short header."""
    getvars = getattr(pack, "all_context_vars", None)
    vars_ = getvars() if callable(getvars) else []
    rendered = [
        render_context_var(str(getattr(pack, "id", "skill")), v)
        for v in vars_
        if isinstance(v, dict)
    ]
    if not rendered:
        return ""
    return (
        "Live workspace state for this skill (authoritative — recomputed "
        "every turn):\n" + "\n".join(rendered)
    )
