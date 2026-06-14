"""File-based subagent definitions — ``.claude/agents/*.md`` (Claude-Code-faithful).

One markdown file per subagent: a leading ``---`` frontmatter block carries
``name`` / ``description`` / ``tools`` (comma list) / ``model`` / ``max_tokens`` / ``hops``;
the body markdown is the worker's ``instructions`` (its system prompt). ``name`` defaults to
the file stem. This mirrors Claude Code's ``.claude/agents/<name>.md``.

Pure stdlib (``pathlib``) — keeps the leaf-isolation invariant (``tests/test_sdk_isolation``).
Reuses :func:`agent_sdk.skills.parser.split_frontmatter` so there is one frontmatter parser.
"""

from __future__ import annotations

from pathlib import Path

from agent_sdk.skills.parser import split_frontmatter
from agent_sdk.subagents.definition import Subagent

__all__ = ["parse_agent_markdown", "load_agents_dir"]


def parse_agent_markdown(text: str, *, default_name: str = "") -> Subagent:
    """Parse one ``.claude/agents/*.md`` document into a :class:`Subagent`."""
    fields, body = split_frontmatter(text)
    row: dict[str, object] = dict(fields)
    row["name"] = (fields.get("name") or default_name).strip()
    # The body IS the worker's system prompt; frontmatter may also carry an explicit one.
    row.setdefault("instructions", "")
    if body.strip():
        row["instructions"] = body.strip()
    elif fields.get("prompt"):
        row["instructions"] = fields["prompt"]
    return Subagent.from_row(row)


def load_agents_dir(path: str | Path) -> list[Subagent]:
    """Load every ``*.md`` under ``path`` as a subagent (sorted by name; missing dir ⇒ [])."""
    base = Path(path)
    if not base.is_dir():
        return []
    agents: list[Subagent] = []
    for md in sorted(base.glob("*.md")):
        try:
            text = md.read_text(encoding="utf-8")
        except OSError:
            continue
        agent = parse_agent_markdown(text, default_name=md.stem)
        if agent.name:
            agents.append(agent)
    return agents
