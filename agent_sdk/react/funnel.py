"""Per-hop observation tiering — the heart of PreAct.

The largest source of hop-over-hop bloat is raw tool output: a ``retrieve_kb``
12-chunk observation (~4k tokens) injected into every later hop is fatal to
density. :func:`tier_observations` keeps the most recent observation(s) FULL
(the model is acting on them now) and demotes older, *spent* observations to a
one-line hint — the conclusion of that action, not its raw payload — while
keeping them discoverable and re-fetchable (Tier 2/3).

Critically, the ``tool_use`` ⇄ ``tool_result`` ``tool_use_id`` pairing is
preserved: the assistant ``tool_use`` blocks stay intact and only the
``tool_result`` *content* shrinks, so the next provider call never sees an
orphaned ``tool_use`` (Anthropic 400s on that). The stable prefix (system +
tool schemas) is the ``system`` param — untouched here — so the prompt cache
survives hop to hop; only this message tail churns.

Pure and deterministic: returns a NEW message list, never mutates the input.
Idempotent — re-tiering an already-demoted observation is a no-op (the spent
marker is the sentinel).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from agent_sdk.network.context_builder import DEFAULT_NODE_WEIGHTS, score_relevance
from agent_sdk.skills import est_tokens

SPENT_MARKER = "[spent observation]"


def _stringify(content: Any) -> str:
    """A tool_result's content as text (it may be a str or a list of blocks)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                parts.append(str(block.get("text", "") or ""))
            else:
                parts.append(str(block))
        return "\n".join(p for p in parts if p)
    return str(content or "")


def _brief_args(inp: Any, limit: int = 60) -> str:
    try:
        s = json.dumps(inp, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        s = str(inp)
    s = " ".join(s.split())
    return s[:limit] + ("…" if len(s) > limit else "")


def _excerpt(text: str, limit: int) -> str:
    s = " ".join((text or "").split())
    return s[:limit] + ("…" if len(s) > limit else "")


def _default_hint(name: str, inp: Any, content: str, hint_max_chars: int) -> str:
    """The spent-observation one-liner: what was called, and its gist."""
    call = f"{name}({_brief_args(inp)})" if name else "tool call"
    return (
        f"{SPENT_MARKER} {call} → {_excerpt(content, hint_max_chars)} "
        "(re-run the tool or read the source to expand)"
    )


def _tool_use_index(messages: list[dict]) -> dict[str, dict]:
    """Map tool_use_id → {name, input} from every assistant tool_use block, so a
    demoted tool_result can name the tool that produced it."""
    index: dict[str, dict] = {}
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                tid = str(block.get("id") or "")
                if tid:
                    index[tid] = {
                        "name": block.get("name") or "",
                        "input": block.get("input") or {},
                    }
    return index


def _is_observation(msg: dict) -> bool:
    content = msg.get("content")
    return (
        msg.get("role") == "user"
        and isinstance(content, list)
        and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)
    )


def _is_error_obs(msg: dict) -> bool:
    """A tool_result observation carrying an error — high-signal for the next
    hop (the model is deciding whether to retry/abandon), so it must not funnel
    out by mere age."""
    for block in msg.get("content", []):
        if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("is_error"):
            return True
    return False


def _obs_tool_ids(msg: dict) -> set[str]:
    return {
        str(b.get("tool_use_id") or "")
        for b in msg.get("content", [])
        if isinstance(b, dict) and b.get("type") == "tool_result"
    }


def tier_observations(
    messages: list[dict],
    *,
    hop: int = 0,
    keep_last_full: int = 1,
    hint_max_chars: int = 160,
    summarize: Callable[[str, Any, str], str] | None = None,
    keep_full_ids: set[str] | None = None,
    keep_errors_full: bool = True,
) -> list[dict]:
    """Funnel the message tail: keep the newest ``keep_last_full`` observation
    message(s) full; demote older ones' ``tool_result`` content to a one-line
    hint (preserving ``tool_use_id``).

    Recency is not the only thing that earns a full slot — funneling purely by
    age silently drops an old-but-critical observation (a stated constraint, a
    connection result) the moment a newer one arrives. Two value signals override
    age:

    * ``keep_full_ids`` — tool_use_ids whose observations stay full regardless of
      age (the caller scores these by CDS/utility; the PreAct value-aware
      demotion). With ``keep_last_full=0`` these are the *only* full observations,
      so a low-value newest observation can itself be demoted.
    * ``keep_errors_full`` — error observations stay full (default): the model is
      mid-decision about retrying, so the failure detail must survive the funnel.

    ``summarize(name, input, raw_content) -> str`` optionally builds a richer
    hint (e.g. a memo) than the default truncation. Returns a new message list.
    """
    obs_positions = [i for i, m in enumerate(messages) if _is_observation(m)]
    keep_full_ids = set(keep_full_ids or ())

    # Positions that earn a full slot: the newest `keep_last_full`, plus any
    # value-pinned or (by default) error observation regardless of age.
    keep_positions: set[int] = set(obs_positions[-keep_last_full:]) if keep_last_full > 0 else set()
    for i in obs_positions:
        if keep_full_ids & _obs_tool_ids(messages[i]):
            keep_positions.add(i)
        if keep_errors_full and _is_error_obs(messages[i]):
            keep_positions.add(i)

    demote_positions = set(obs_positions) - keep_positions
    if not demote_positions:
        return list(messages)  # nothing spent yet — everything kept earns its slot
    tool_index = _tool_use_index(messages)

    out: list[dict] = []
    for i, msg in enumerate(messages):
        if i not in demote_positions:
            out.append(msg)
            continue
        new_content = []
        for block in msg.get("content", []):
            if not (isinstance(block, dict) and block.get("type") == "tool_result"):
                new_content.append(block)
                continue
            tid = str(block.get("tool_use_id") or "")
            raw = _stringify(block.get("content"))
            if raw.startswith(SPENT_MARKER):
                new_content.append(block)  # idempotent — already demoted
                continue
            meta = tool_index.get(tid, {})
            name, inp = meta.get("name", ""), meta.get("input", {})
            hint = (
                summarize(name, inp, raw)
                if summarize is not None
                else _default_hint(name, inp, raw, hint_max_chars)
            )
            demoted = dict(block)
            demoted["content"] = hint  # shrink content only; tool_use_id preserved
            new_content.append(demoted)
        out.append({**msg, "content": new_content})
    return out


COMPACTION_MARKER = "[compacted earlier tool results]"


def _obs_is_spent(msg: dict) -> bool:
    for block in msg.get("content", []):
        if (
            isinstance(block, dict)
            and block.get("type") == "tool_result"
            and _stringify(block.get("content")).startswith(SPENT_MARKER)
        ):
            return True
    return False


def _merge_consecutive(messages: list[dict]) -> list[dict]:
    """Coalesce adjacent same-role messages (removing message pairs can leave two
    user or two assistant turns adjacent — the provider requires alternation).
    Content is normalized to a block list and concatenated."""
    out: list[dict] = []
    for m in messages:
        c = m.get("content")
        blocks = c if isinstance(c, list) else [{"type": "text", "text": str(c)}]
        if out and out[-1].get("role") == m.get("role"):
            out[-1] = {**out[-1], "content": out[-1]["content"] + blocks}
        else:
            out.append({**m, "content": list(blocks)})
    return out


def compact_observations(
    messages: list[dict],
    *,
    keep_last_full: int = 1,
    keep_full_ids: set[str] | None = None,
    keep_errors_full: bool = True,
    max_spent: int = 6,
    summary_lines: int = 4,
    hint_max_chars: int = 160,
    summarize: Callable[[str, Any, str], str] | None = None,
) -> list[dict]:
    """Bound the tool-loop tail — the compaction tier of PreAct.

    :func:`tier_observations` reduces the GROWTH RATE (spent observations shrink to
    hints) but not the CEILING: hints are never collected, so the tail is O(hops).
    Over a long run (hundreds of tool calls) that floods the window. This compacts:
    after funneling, the spent-hint *pairs* older than the most recent ``max_spent``
    are ELIMINATED (their ``tool_use`` + ``tool_result`` removed together, so no
    orphan) and folded into ONE bounded rolling summary (at most ``summary_lines``
    recent digests + a running offloaded count). The full bodies live in external
    memory and are re-fetchable by re-running the tool — so the trail stays on
    track while the window stays O(working set + max_spent), not O(hops).

    Pure/deterministic. Pinned (``keep_full_ids``) and error observations are kept
    full and never eliminated.
    """
    tiered = tier_observations(
        messages,
        keep_last_full=keep_last_full,
        keep_full_ids=keep_full_ids,
        keep_errors_full=keep_errors_full,
        hint_max_chars=hint_max_chars,
        summarize=summarize,
    )
    spent = [i for i, m in enumerate(tiered) if _is_observation(m) and _obs_is_spent(m)]
    if len(spent) <= max_spent:
        return tiered  # tail still within the working-set bound

    excess, retained = spent[:-max_spent], spent[-max_spent:]
    excess_tids: set[str] = set()
    digests: list[str] = []
    for i in excess:
        for b in tiered[i].get("content", []):
            if isinstance(b, dict) and b.get("type") == "tool_result":
                excess_tids.add(str(b.get("tool_use_id") or ""))
                txt = _stringify(b.get("content")).replace(SPENT_MARKER, "").strip()
                digests.append(" ".join(txt.split())[:120])

    n = len(digests)
    kept = digests[-summary_lines:]
    summary = (
        f"{COMPACTION_MARKER} {n} earlier tool results offloaded to memory "
        "(re-run the tool to retrieve any):\n"
        + "\n".join(f"· {d}" for d in kept)
        + (f"\n· (+{n - len(kept)} more earlier calls offloaded)" if n > len(kept) else "")
    )
    attach = retained[0] if retained else None

    out: list[dict] = []
    attached = False
    excess_set = set(excess)
    for i, m in enumerate(tiered):
        if i in excess_set:
            continue  # eliminate the spent observation (offloaded)
        if m.get("role") == "assistant" and isinstance(m.get("content"), list):
            blocks = [
                b
                for b in m["content"]
                if not (
                    isinstance(b, dict)
                    and b.get("type") == "tool_use"
                    and str(b.get("id") or "") in excess_tids
                )
            ]
            if not blocks:
                continue  # message held only eliminated tool_uses
            m = {**m, "content": blocks}
        if i == attach and not attached:
            m = {**m, "content": [{"type": "text", "text": summary}, *m["content"]]}
            attached = True
        out.append(m)
    if not attached:  # max_spent == 0 or attach eliminated — fold into the newest obs
        for j in range(len(out) - 1, -1, -1):
            if _is_observation(out[j]):
                out[j] = {
                    **out[j],
                    "content": [{"type": "text", "text": summary}, *out[j]["content"]],
                }
                break
    return _merge_consecutive(out)


# ── Working-set discipline: value-aware pinning + a budget-driven tail ─────────
#
# ``tier_observations`` demotes by recency (newest full, older→hints). That bounds
# the growth *rate* but the spent-hint tail is still O(hops), and recency alone
# silently demotes an OLD-but-critical observation the moment a newer one lands.
# ``score_observations`` scores each spent observation against the current goal
# (CDS = relevance / cost) so value, not age, decides which stay full — feeding
# ``keep_full_ids``. ``obs_tail_tokens`` measures the tail so the loop can compact
# only when it actually exceeds a working-set budget (not on a fixed hop count).


def obs_tail_tokens(messages: list[dict]) -> int:
    """Estimated tokens of all tool-result (observation) content in the tail.

    The signal the working-set budget gates on — when this exceeds the budget,
    the loop compacts. Counts only observations (the churn), not the stable
    system/tool-schema prefix."""
    total = 0
    for msg in messages:
        if not _is_observation(msg):
            continue
        for b in msg.get("content", []):
            if isinstance(b, dict) and b.get("type") == "tool_result":
                total += est_tokens(_stringify(b.get("content")))
    return total


def score_observations(
    messages: list[dict],
    *,
    goal: str,
    embed_one: Callable | None = None,
    weights: dict[str, float] | None = None,
    keep_top: int = 4,
) -> set[str]:
    """The ``tool_use_id``s of the highest-CDS spent observations w.r.t. ``goal``.

    CDS = relevance(goal, observation) / cost_norm(tokens) — the same value model
    the context tiers use, applied to tool output. Returns the top-``keep_top``
    ids to pin FULL via ``keep_full_ids`` so a goal-critical-but-old observation
    survives a newer-but-off-goal one (value beats recency). Deterministic;
    ``embed_one=None`` ⇒ L1 (lexical) only."""
    if keep_top <= 0:
        return set()
    w = weights or DEFAULT_NODE_WEIGHTS
    cost_unit = w.get("cds_cost_unit", 40.0) or 40.0
    index = _tool_use_index(messages)
    q_vec = embed_one(goal) if (embed_one is not None and goal) else None

    scored: list[tuple[float, int, str]] = []
    order = 0
    for msg in messages:
        if not _is_observation(msg):
            continue
        for b in msg.get("content", []):
            if not (isinstance(b, dict) and b.get("type") == "tool_result"):
                continue
            tid = str(b.get("tool_use_id") or "")
            if not tid:
                continue
            content = _stringify(b.get("content"))
            meta = index.get(tid, {})
            text = f"{meta.get('name', '')} {_brief_args(meta.get('input', {}))} {content}"
            sc = score_relevance(goal or "", q_vec, text, embed_one=embed_one, weights=w)
            cost_norm = max(1.0, est_tokens(content) / cost_unit)
            cds = float(sc["activation"]) / cost_norm
            scored.append((cds, order, tid))
            order += 1
    scored.sort(key=lambda t: (-t[0], t[1]))
    return {tid for _, _, tid in scored[:keep_top]}
