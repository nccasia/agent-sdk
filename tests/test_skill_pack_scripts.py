"""A pack carries the scripts its bundle declares (c0093, flow 3).

Pure: a pack is built from a row, no runtime and no sandbox. What *running* a
declared script does is `agent-core`'s half of the flow
(`packages/agent-core/tests/sdk/test_native_sandbox.py`).
"""

from __future__ import annotations

from agent_sdk.skill_def import Skill
from agent_sdk.skills.packs import SkillPack, SkillRegistry


def _row(**over) -> dict:
    """A minimal skill row, in the shape the cli's SQL select produces."""
    row = {
        "slug": "analysis",
        "name": "Analysis",
        "description": "Runs cohort analysis.",
        "stages": ["synthesize"],
        "instructions": "Analyse the cohort.",
        "files": {"scripts/analyze.py": "print(4)"},
        "injection": "on_demand",
    }
    row.update(over)
    return row


DECLARED = [
    {
        "name": "analyze",
        "path": "scripts/analyze.py",
        "language": "python",
        "description": "Computes a delta from two numbers.",
    }
]


def test_a_pack_without_scripts_carries_an_empty_declaration():
    """Flow 3. Every skill that exists today — a pre-c0092 row, or a code-first
    plugin pack — must carry an empty declaration rather than be an error, or
    this change breaks every bot that has a skill.

    Asserted two ways on purpose: the direct construction (plugin packs and
    `_BUILTIN_SKILLS` never touch `from_rows`) and the row path (DB skills)."""
    assert SkillPack(id="x", stages=(), instructions="").scripts == ()

    registry = SkillRegistry.from_rows([_row()])
    pack = registry.get("analysis")
    assert pack is not None
    assert pack.scripts == ()


def test_a_pack_carries_its_declared_scripts():
    """The positive half: a declared script reaches the engine in the resolved
    read shape, with `name` and `language` as the row carries them — the engine
    re-derives nothing (`interface.md` §1)."""
    registry = SkillRegistry.from_rows([_row(scripts=DECLARED)])
    pack = registry.get("analysis")
    assert pack is not None

    assert [(s.name, s.path, s.language, s.description) for s in pack.scripts] == [
        ("analyze", "scripts/analyze.py", "python", "Computes a delta from two numbers.")
    ]


def test_str_json_scripts_are_tolerated():
    """The other JSON columns already tolerate a str-JSON value from a raw SQL
    row (`packs.py:99-121`); `scripts` is a JSON column too, so it gets the same
    treatment. A row that arrives as text must not silently yield no scripts —
    that would make a declared script unrunnable for reasons invisible in the
    data."""
    import json

    registry = SkillRegistry.from_rows([_row(scripts=json.dumps(DECLARED))])
    pack = registry.get("analysis")
    assert pack is not None
    assert [s.name for s in pack.scripts] == ["analyze"]


def test_unparseable_scripts_degrade_to_no_declaration():
    """The other half of that tolerance: malformed text yields `()`, not an
    exception. A turn must not fail to start because one column will not parse."""
    registry = SkillRegistry.from_rows([_row(scripts="{not json")])
    pack = registry.get("analysis")
    assert pack is not None
    assert pack.scripts == ()


def test_the_sdk_skill_facade_is_not_the_carrier():
    """Pins `design.md` §1 so the boundary cannot rot silently.

    `Skill.to_pack()` builds a `SkillPack` from its own fields, and it has no
    `scripts`. That is deliberate — the SDK's `Skill` is the authoring façade and
    the engine's source of truth is the pack — but it means a round trip through
    the façade drops a declaration. If a later change routes scripts through
    `Skill`, this test is what says the decision was made on purpose."""
    skill = Skill("analysis", when="analysis", files={"scripts/analyze.py": "print(4)"})
    assert not hasattr(skill, "scripts")

    # A pack that declares a script, routed through the façade, comes back with
    # none — which is why the runtime reads packs and this is not a bug to fix
    # by adding the field to `Skill`.
    pack = SkillRegistry.from_rows([_row(scripts=DECLARED)]).get("analysis")
    assert pack is not None
    assert [s.name for s in pack.scripts] == ["analyze"]
    assert skill.to_pack().scripts == ()
