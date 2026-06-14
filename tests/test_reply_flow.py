"""Reply flow — a pinned response stage that continues the conversation.

The terminal stage is the *response stage*: a dedicated, pinned ``respond`` lobe frames it to
write the NEXT reply to the user's latest message using the information gathered this turn (the
notes), continuing the dialogue rather than re-greeting. Prior turns render as a trimmed
transcript (primacy + recency).
"""

from __future__ import annotations

from agent_sdk import PreactAgent
from agent_sdk.clients.fake import FakeClient
from agent_sdk.session import SessionState, Turn


def _agent() -> PreactAgent:
    return PreactAgent(client=FakeClient(default="ok"), universal_memory=False)


def _system(agent, *, is_last: bool, notes=None) -> str:
    eng = agent.engine
    stage = eng.stage_registry.stages()[0]
    return eng._compose_system(stage, {"query": "hi"}, SessionState(), notes or [], is_last=is_last)


# ── the response stage ─────────────────────────────────────────────────────────
def test_respond_lobe_is_injected():
    assert "respond" in _agent().engine.lobe_by_id


def test_respond_is_a_real_registered_production_lobe():
    from agent_sdk.lobes.network import default_lobe_objects

    assert "respond" in {lb.id for lb in default_lobe_objects()}


def test_respond_lobe_emits_two_framing_parts_not_the_transcript():
    """The lobe emits TWO framing contributions (continuation rules + voice/next-step), both
    tagged ``respond``. The conversation lives once — in the ``messages`` array — so the lobe
    never re-injects the transcript into the system prompt (de-dup; cache prefix stable)."""
    from agent_sdk.contracts.turn import TurnContext
    from agent_sdk.expression.lobes.respond import LOBE

    state = SessionState(history=[Turn("user", "what is X?"), Turn("assistant", "X is foo")])
    contribs = LOBE.prompt(TurnContext(query="and Y?", session_memory=state))
    assert [c.source for c in contribs] == ["respond", "respond"]  # two framing parts
    assert "what is X?" not in "".join(c.text for c in contribs)  # transcript NOT in the prompt

    # no dialog available → still just the framing parts (never a transcript chunk)
    empty = LOBE.prompt(TurnContext(query="hi", session_memory=SessionState()))
    assert {c.source for c in empty} == {"respond"} and len(empty) == 2


def test_respond_step_is_a_real_stage_module():
    # a flow can list a real respond stage as its terminal ("flow decides stages")
    from agent_sdk.expression.stages import respond_step

    step = respond_step("qna")
    assert step.name == "respond"
    assert "respond" in step.lobes


def test_real_respond_stage_renders_once_no_double():
    from agent_sdk.stages import stage

    agent = _agent()
    eng = agent.engine
    respond_stage = stage("respond", lobes=["respond"], loop="single")
    sys = eng._compose_system(respond_stage, {"query": "hi"}, SessionState(), [], is_last=True)
    # the real stage renders the framing via the lobe loop; the engine must NOT also pin it
    assert sys.count("continuing this conversation") == 1


def test_respond_framing_only_on_terminal_stage():
    agent = _agent()
    last = _system(agent, is_last=True)
    not_last = _system(agent, is_last=False)
    assert "continuing this conversation" in last
    assert "<respond>" in last  # XML-tagged response section
    assert "continuing this conversation" not in not_last  # collectors don't get it


def test_response_framing_leads_the_volatile_notes_tail():
    agent = _agent()
    sys = _system(agent, is_last=True, notes=["[research] Zephyr ships Friday"])
    assert "Zephyr ships Friday" in sys
    # canonical layer order (cache-prefix): the stable response framing leads; the turn-volatile
    # notes trail in the tail. The framing is position-independent ("notes gathered this turn").
    assert sys.index("continuing this conversation") < sys.index("Zephyr ships Friday")


def test_no_regreet_framing():
    sys = _system(_agent(), is_last=True)
    assert "re-greet" in sys or "re-introduce" in sys


# ── trimmed transcript (primacy + recency) ─────────────────────────────────────
def test_short_history_is_verbatim():
    st = SessionState(history=[Turn("user", "hi"), Turn("assistant", "hello")])
    assert st.messages() == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]


def test_long_history_is_trimmed_primacy_and_recency():
    st = SessionState()
    for i in range(10):
        st.history.append(Turn("user", f"Q{i} " + ("x" * 5000 if i == 5 else "")))
        st.history.append(Turn("assistant", f"A{i}"))
    msgs = st.messages(first_n=1, last_m=4, max_turn_chars=200)
    # one "[Conversation so far]" digest block + 4 recent turns
    assert len(msgs) == 5
    head = msgs[0]
    assert head["role"] == "user" and head["content"].startswith("[Conversation so far]")
    assert "Q0" in head["content"]  # primacy kept
    assert "earlier turns elided" in head["content"]  # middle blurred
    # recency capped — the 5000-char turn never appears whole
    assert all("x" * 300 not in m["content"] for m in msgs)


def test_summary_is_folded_into_the_digest_block():
    st = SessionState(
        summary="earlier: we discussed the migration",
        history=[Turn("user", "and the date?"), Turn("assistant", "Saturday")],
    )
    msgs = st.messages()
    assert msgs[0]["content"].startswith("[Conversation so far]")
    assert "migration" in msgs[0]["content"]


# ── two-turn continuation (no re-greet, prior turn in context) ─────────────────
async def test_second_turn_continues_the_conversation():
    from agent_sdk.session import Session
    from agent_sdk.stores.session import SessionStoreInMemory

    agent = PreactAgent(
        client=FakeClient(["first answer", "second answer"]),
        session=Session("c1", SessionStoreInMemory()),
        universal_memory=False,
    )
    await agent.query("what is X?")
    await agent.query("and Y?")
    turn2_msgs = agent.client.calls[-1]["messages"]
    blob = "\n".join(str(m.get("content")) for m in turn2_msgs)
    assert "what is X?" in blob and "first answer" in blob  # prior turn is in context
    # the terminal system framed the reply as a continuation
    assert "continuing this conversation" in agent.client.calls[-1]["system"]


# ── override: a plugin replaces the respond renderer ───────────────────────────
def test_respond_lobe_is_overridable_by_a_plugin():
    """A plugin contributes a lobe with id="respond"; it REPLACES the builtin (plugin-wins by
    id) and its voice flows into the pinned terminal framing."""
    from agent_sdk.expression.lobes.respond import RespondLobe

    class WarmRespond(RespondLobe):
        STYLE = "Reply warmly, like a friendly mentor."  # tweak one part — recomposes

    class RespondOverride:
        name = "respond_override"
        def install(self, setup):
            setup.add_lobe(WarmRespond())

    base = PreactAgent(client=FakeClient(default="ok"), universal_memory=False)
    assert type(base.engine.lobe_by_id["respond"]).__name__ == "RespondLobe"

    agent = PreactAgent(client=FakeClient(default="ok"), universal_memory=False,
                        plugins=[RespondOverride()])
    r = agent.engine.lobe_by_id["respond"]
    assert type(r).__name__ == "WarmRespond"
    assert sum(1 for lb in agent.engine.lobes if lb.id == "respond") == 1  # replaced, not doubled
    assert "warmly, like a friendly mentor" in r.system_prompt  # part recomposed
    assert "do not restart" in r.system_prompt  # continuation contract kept
    # the override's voice reaches the pinned terminal framing (fallback path uses system_prompt)
    assert "warmly, like a friendly mentor" in _system(agent, is_last=True)


def test_history_window_is_configurable_per_agent():
    a = PreactAgent(client=FakeClient(default="ok"), universal_memory=False,
                    history_window={"first_n": 1, "last_m": 2, "max_turn_chars": 100})
    assert a.engine._history_window == {"first_n": 1, "last_m": 2, "max_turn_chars": 100}
    # default agent uses SessionState.messages() defaults (empty window)
    b = PreactAgent(client=FakeClient(default="ok"), universal_memory=False)
    assert b.engine._history_window == {}
