# Contributing to agent-sdk

Thanks for your interest in improving agent-sdk. This guide covers the dev setup, the invariants
every change must keep, and the gates a PR has to pass.

## Dev setup

```bash
git clone <repo> && cd agent-sdk
uv sync                         # or: pip install -e ".[dev]"
uv run python -m pytest -q      # run the suite
```

Python 3.12+ is required. The optional extras are `openai` and `redis`.

## Gates (run these before opening a PR)

```bash
uv run python -m pytest -q          # all tests must pass
uv run ruff check agent_sdk         # lint must be clean
uv run ruff format agent_sdk        # formatting
```

CI runs the same commands. New behavior needs a test; new public API needs a doc note in
[`docs/api.md`](./docs/api.md).

## Invariants — do not break these

These are load-bearing properties of the SDK. A change that violates one is a regression, not a
trade-off:

1. **Leaf isolation.** `agent_sdk` imports only the stdlib, its third-party deps (`anthropic`,
   `numpy`, `pydantic`, `cachetools`, optionally `openai` / `redis`), and other `agent_sdk`
   modules — never a host application package. Gated by `tests/test_sdk_isolation.py`.
2. **Default-network parity.** An agent with no extra plugins must be byte-identical to the default
   network: `default_lobe_objects()` returns the same canonical lobes in the same `(layer, order)`.
   New default capability is a registry row / plugin, **never** an interpreter branch.
3. **Citations-mandatory.** The output-contract lobes `cite` / `filter` (pinned) and `synthesize`
   survive removal — no plugin, weight, or removal can strip the ground-or-refuse guarantee. (The
   default-on `SafetyPlugin` can be *disabled* deliberately by an integrator, but cannot be
   *stripped* by another plugin.)
4. **Determinism in the core.** Intent recognition, activation, attention/budget, and flow
   resolution are pure functions of `(spec, context)` — never an LLM judging the pipeline.
5. **Benches are live-only.** No LLM stubs in `benchmarks/` — a stubbed bench is an integration
   test and belongs in `tests/`.

## The core / extension boundary

- **Core** lobes live in `agent_sdk/lobes/` (cognition, tools, skills, task, memory, reply). They
  are intrinsic and not toggleable.
- **Extensions** live in `agent_sdk/plugins/`, one folder per plugin, each owning its lobes /
  stages / flows / skills / tools. Default-on toggleable extensions (`SafetyPlugin`,
  `FormatPlugin`) round out the production network; the rest are opt-in integrations.

When adding a capability, ask: is it intrinsic to *every* agent (core) or an optional add-on
(extension)? When in doubt, prefer an extension — it keeps the core lean and the capability
toggleable. See [`docs/concepts/10-plugins.md`](./docs/concepts/10-plugins.md).

## Commit & PR conventions

- Conventional-commit style subjects: `feat(...)`, `fix(...)`, `refactor(...)`, `docs(...)`.
- Keep PRs scoped; describe the invariant(s) the change touches and how you verified them.
- By contributing you agree your contributions are licensed under the project's
  [Apache-2.0](./LICENSE) license.
