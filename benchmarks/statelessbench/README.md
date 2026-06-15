# statelessbench

Proves the SDK serves **stateless**: one JSON snapshot per session (conversation + universal
memory), one store, and a pooled queue worker that does `load → turn → offload` by session id.

- `python benchmarks/statelessbench/run.py --report --label base` — free deterministic gate (no provider).
- Method + gates: [`METHOD.md`](./METHOD.md).

It's a **free** bench (FakeClient), so it runs in the deterministic CI ladder alongside
attentionbench/flowbench/corgictionbech.
