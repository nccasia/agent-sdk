"""format — B5 style lobe: channel-shaped answer formatting.

Behavior: restyles the final answer for fixed-format surfaces
(`_should_format_response` — any channel with markdown/length constraints). Fires
iff the policy declares a fixed format; the `task_execute` path biases it +0.2
(fired-task output is delivered to a channel). Domain-free: the channel's concrete
constraints are a policy field (`format_constraints`), not a hardcoded deployment.

Tuning keys: `prior_format` (0), `min_format` (0.5), `w_fixed_format` (1.0),
`path_task_execute__format` (0.2).
Gates: degenerate-parity matrix; format goldens in worker tests.
"""

from __future__ import annotations

from agent_sdk.lobes.runtime import (
    LlmCall,
    Lobe,
    PromptContribution,
    TurnContext,
    extract_text,
)
from agent_sdk.network.activation import LAYER_EXPRESSION

# ── Behavior contract ────────────────────────────────────────────────────────
# build_requirements(policy, deployment_id=...) -> str — PURE assembly of the
#   language/tone/voice/format requirement lines from policy fields, plus any
#   free-text channel constraints the host supplies in `policy.format_constraints`.
# run(llm, *, answer, requirements, max_tokens) -> str — the rewrite pass;
#   empty output ships the original answer; a raised call is handled by the
#   orchestration (degrade to the unformatted answer, never lose the turn).
# Model resolves via the filter stage (legacy repurposing).

LANGUAGE_NAMES: dict[str, str] = {
    "en": "English",
    "vi": "Vietnamese (tiếng Việt)",
    "ja": "Japanese (日本語)",
    "zh": "Chinese (中文)",
    "ko": "Korean (한국어)",
    "fr": "French (français)",
    "de": "German (Deutsch)",
    "es": "Spanish (español)",
}

SYSTEM_PROMPT = """You are a response formatter. Rewrite the provided answer according to the requirements below.
Preserve all factual content, citations [chunk_id](source_ref), and logical structure. Do NOT add, remove, or change any facts.
Only change the language, tone, and formatting as specified.

{requirements}

Rewrite the answer below to meet ALL requirements. Output ONLY the rewritten answer, nothing else."""

USER_TEMPLATE = "Answer to rewrite:\n\n{answer}"


def build_requirements(policy: dict, *, deployment_id: str | None = None) -> str:
    """The requirement lines for the rewrite — a PURE function of policy fields.

    Domain-free: language/tone/voice/format come from named policy fields, and a
    host can append ANY channel-specific constraint via ``policy.format_constraints``
    (free text — e.g. a chat surface's limited-markdown rules). The SDK names no
    deployment; a platform layer (e.g. agent-core for Mezon) supplies the constraint
    text. ``deployment_id`` is kept for the caller's context but is not branched on."""
    reqs: list[str] = []
    lang = policy.get("language", "en")
    if lang != "en":
        full_name = LANGUAGE_NAMES.get(lang, lang)
        reqs.append(
            f"LANGUAGE: Write the ENTIRE response in {full_name}. "
            f"Every word, heading, bullet point, and explanation must be in {full_name}. "
            f"Only keep proper nouns, code snippets, and file paths in English."
        )
    tone = policy.get("tone", "professional")
    if tone != "professional":
        reqs.append(f"TONE: Use a {tone} tone throughout.")
    voice = policy.get("voice", "")
    if voice:
        reqs.append(f"VOICE: {voice}")
    fmt = policy.get("response_format", "markdown")
    if fmt != "markdown":
        reqs.append(f"FORMAT: Structure the response as {fmt}.")
    constraints = policy.get("format_constraints")
    if constraints:
        reqs.append(str(constraints).strip())
    return "\n".join(reqs)


async def run(llm: LlmCall, *, answer: str, requirements: str, max_tokens: int = 2048) -> str:
    """Rewrite the answer to the target language/tone/voice/format.
    Legacy-exact: temperature=0, usage rolled up, empty output → original."""
    response = await llm(
        stage="filter",
        system=SYSTEM_PROMPT.format(requirements=requirements),
        messages=[{"role": "user", "content": USER_TEMPLATE.format(answer=answer)}],
        max_tokens=max_tokens,
        temperature=0,
    )
    return extract_text(response) or answer


class FormatLobe(Lobe):
    """Restyle the final answer for fixed-format surfaces (language/tone/voice/
    channel constraints) — a second LLM rewrite pass over the composed answer."""

    id = "format"
    name = "Format"
    description = "Rewrite the final answer to the channel's required language/tone/format."
    use_when = "the bot has a non-default language, tone, voice, or output format"
    how = (
        "A second LLM pass rewrites the composed answer to meet the bot's format "
        "requirements (built by build_requirements — language/tone/voice + any "
        "policy.format_constraints). Empty output ships the original; a raised call "
        "degrades to the unformatted answer (never lose the turn)."
    )
    system_prompt = SYSTEM_PROMPT
    user_template = USER_TEMPLATE
    behavior = "style"
    layer = LAYER_EXPRESSION
    order = 2
    writes = ("formatted_answer",)
    # Back-compat module-API members (referenced as LOBE.<NAME>).
    SYSTEM_PROMPT = SYSTEM_PROMPT
    USER_TEMPLATE = USER_TEMPLATE

    def activation(self, ctx: dict) -> float:
        return 1.0 if ctx.get("fixed_format") else 0.0

    def prompt(self, _ctx: TurnContext) -> list[PromptContribution]:
        return [PromptContribution(SYSTEM_PROMPT, stability="stable", source=self.id)]

    def build_requirements(
        self,
        policy: dict,
        *,
        deployment_id: str | None = None,
        _ctx: TurnContext | None = None,
    ) -> str:
        return build_requirements(policy, deployment_id=deployment_id)

    async def run(
        self,
        llm: LlmCall,
        *,
        answer: str,
        requirements: str,
        max_tokens: int = 2048,
        _ctx: TurnContext | None = None,
    ) -> str:
        return await run(llm, answer=answer, requirements=requirements, max_tokens=max_tokens)


LOBE = FormatLobe()
SPEC = LOBE.spec  # back-compat export
