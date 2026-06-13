#!/usr/bin/env python3
"""Generate a realistic, complex memory dataset for memory-bench.

Simulates a long-running engineering-team assistant's accumulated memory — facts, decisions, tool
results, temp files — over a busy session, with the messy structure that stresses recall:

* **recurring entities** (projects · people · services) so attributes collide across entities,
* **temporal evolution** (a deadline moves; a deploy window is superseded) so "current" ≠ newest-stored,
* **paraphrase** queries — some using the in-vocab concept synonyms (semantic recall SHOULD handle),
  some using out-of-vocab synonyms (the improvement EDGE: needs a real embedder),
* **distractors** (same predicate, different entity), **multi-needle** (a synthesis needs K facts),
  **multi-hop** (chain two lookups — an edge a single recall can't do).

Deterministic (seeded). Writes `dataset/entries.jsonl` + `dataset/queries.jsonl` — committed as the
bench's source of truth. Scale with `--n` (entries); the query set is fixed-shape, scenario-labeled.

    python dataset/gen_dataset.py            # the committed default (≈400 entries)
    python dataset/gen_dataset.py --n 5000   # a bigger corpus (same query shapes)
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))  # packages/agent-sdk — for the concept vocab

from benchmarks._shared.embed import CONCEPTS  # noqa: E402

PROJECTS = ["zephyr", "atlas", "orion", "helios", "nova", "titan", "vega"]
PEOPLE = ["@lan", "@minh", "@huy", "@duc", "@mai", "@thuy", "@khoa"]
SERVICES = ["payments-api", "search", "auth", "gateway", "billing", "notifications"]
ROOMS = ["Mercury", "Saturn", "Neptune", "Pluto"]

# In-vocab concept synonyms (the concept embedder knows these) vs out-of-vocab paraphrases (it does
# NOT — these are the improvement edges a real embedder would close).
INVOCAB = {c: sorted(s) for c, s in CONCEPTS.items()}
OUT_OF_VOCAB = {  # natural paraphrases the concept embedder can't match
    "owner": ["accountable", "in charge of", "point person for"],
    "deadline": ["ship date", "target date", "when is", "drop-dead date"],
    "schedule": ["go-live", "release window", "when does", "kickoff"],
    "performance": ["how fast", "response time", "tail latency"],
    "incident": ["the fire", "the sev1", "what broke"],
}


def _w(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")


def generate(n_entries: int, seed: int = 7) -> tuple[list[dict], list[dict]]:
    rng = random.Random(seed)
    entries: list[dict] = []
    queries: list[dict] = []
    seq = 0

    def add(kind, scope, key, content, *, entity=None, concept=None, supersedes=None):
        nonlocal seq
        entries.append({"seq": seq, "kind": kind, "scope": scope, "key": key, "content": content,
                        "entity": entity, "concept": concept, "supersedes": supersedes})
        seq += 1
        return key

    # ── the structured needles (each project gets a coherent record) ────────────
    for p in PROJECTS:
        owner = rng.choice(PEOPLE)
        date = f"2026-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
        day = rng.choice(["Monday", "Wednesday", "Friday"])
        _ = rng.choice(ROOMS)  # room unused; keep the draw so the RNG sequence stays stable
        add("fact", "conversation", f"{p}-deadline", f"The {p} project deadline is {date}.",
            entity=p, concept="deadline")
        add("fact", "conversation", f"{p}-owner", f"{owner} is the owner of the {p} project.",
            entity=p, concept="owner")
        add("fact", "conversation", f"{p}-schedule", f"The {p} rollout is scheduled for {day}.",
            entity=p, concept="schedule")
        add("decision", "conversation", f"{p}-arch",
            f"For {p} we chose a queue-based design because it decouples the workers.", entity=p)
        # multi-needle: three launch requirements per project.
        for i in (1, 2, 3):
            add("fact", "conversation", f"{p}-req-{i}",
                f"Launch requirement {i} for {p}: the {rng.choice(SERVICES)} must pass its checks.",
                entity=p, concept="schedule")

    # ── temporal supersession: a deploy window that moved (stale then current) ──
    super_project = "atlas"
    add("fact", "conversation", f"{super_project}-deploy-v1",
        f"The {super_project} deploy window is Tuesday 10:00 (OLD — rescheduled).",
        entity=super_project, concept="schedule")
    cur_key = add("fact", "conversation", f"{super_project}-deploy-v2",
                  f"The {super_project} deploy window is Thursday 14:00 UTC.",
                  entity=super_project, concept="schedule", supersedes=f"{super_project}-deploy-v1")

    # ── services: SLAs, perf, security (more entities sharing predicates) ───────
    for s in SERVICES:
        add("fact", "conversation", f"{s}-sla", f"{s} SLA: P1 incidents ack within 15 minutes.",
            entity=s, concept="incident")
        add("fact", "conversation", f"{s}-perf", f"{s} p99 latency is {rng.randint(50, 900)}ms.",
            entity=s, concept="performance")
        add("fact", "user", f"{s}-auth", f"{s} requires an auth token for access.",
            entity=s, concept="security")

    # ── user preferences (long-term, cross-scope) ──────────────────────────────
    for person in PEOPLE:
        add("fact", "user", f"{person}-pref",
            f"{person} prefers async standups and dark mode.", entity=person, concept="preference")

    # ── flash working memory: tool results + reasoning temps (this turn) ────────
    for i in range(min(40, n_entries // 8)):
        add("tool_result", "turn", f"kb-{i}",
            f"retrieve_kb result {i}: found a runbook about {rng.choice(SERVICES)} restarts.",
            concept="incident")
        add("note", "turn", f"think-{i}", f"sub-goal: verify {rng.choice(PROJECTS)} owner before pinging.")

    # ── filler to reach scale: realistic but low-signal channel/KB chatter ──────
    while len(entries) < n_entries:
        i = len(entries)
        add("note", "conversation", f"chatter-{i}",
            f"channel note {i}: {rng.choice(PEOPLE)} mentioned the {rng.choice(SERVICES)} dashboard.")

    # ── QUERIES (scenario-labeled; expect_keys are the correct targets) ─────────
    def q(qid, typ, text, expect, difficulty, note=""):
        queries.append({"id": qid, "type": typ, "query": text, "expect_keys": expect,
                        "difficulty": difficulty, "note": note})

    for p in PROJECTS[:5]:
        # exact — same words as stored (lexical AND semantic should nail it).
        q(f"exact-{p}", "exact", f"what is the {p} project deadline", [f"{p}-deadline"], "easy")
        # semantic in-vocab — a concept synonym (semantic SHOULD handle; lexical misses).
        q(f"sem-{p}", "semantic", f"when is the {p} launch planned", [f"{p}-schedule"], "medium",
          "launch/planned ~ rollout/scheduled (in-vocab)")
        # distractor — entity-specific attribute that other entities also have.
        q(f"distract-{p}", "distractor", f"who is the owner of {p}", [f"{p}-owner"], "medium",
          "every project has an owner")
        # multi-needle — all three launch requirements.
        q(f"multi-{p}", "multi_needle", f"list the launch requirements for {p}",
          [f"{p}-req-1", f"{p}-req-2", f"{p}-req-3"], "hard")
        # paraphrase HARD — out-of-vocab synonym (the improvement EDGE).
        para = OUT_OF_VOCAB["owner"][0]
        q(f"para-{p}", "paraphrase_hard", f"who is {para} the {p} project", [f"{p}-owner"], "edge",
          "out-of-vocab paraphrase — concept embedder can't match")

    # supersession — must return the CURRENT deploy window, not the stale one.
    q("super-atlas", "supersession", "what is the current atlas deploy window", [cur_key], "hard",
      "stale v1 must lose to current v2")
    # temporal — 'latest' phrasing (recency).
    q("temporal-atlas", "temporal", "the latest atlas deploy window", [cur_key], "hard")
    # multi-hop — chain: who owns the project launching on atlas's day? (an EDGE: single recall can't chain)
    q("multihop-1", "multi_hop", "who owns the project whose rollout is on the same day as atlas",
      [f"{super_project}-owner"], "edge", "requires chaining two lookups")
    # paraphrase HARD on schedule/perf too.
    q("para-deadline", "paraphrase_hard", "what's the ship date for zephyr", ["zephyr-deadline"], "edge",
      "ship date ~ deadline (out-of-vocab)")
    q("para-perf", "paraphrase_hard", "how fast is the search service", ["search-perf"], "edge",
      "how fast ~ latency (out-of-vocab)")

    return entries, queries


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=400, help="number of entries in the corpus")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    entries, queries = generate(args.n, seed=args.seed)
    _w(HERE / "entries.jsonl", entries)
    _w(HERE / "queries.jsonl", queries)
    by_type: dict[str, int] = {}
    for q in queries:
        by_type[q["type"]] = by_type.get(q["type"], 0) + 1
    print(f"wrote {len(entries)} entries → {HERE / 'entries.jsonl'}")
    print(f"wrote {len(queries)} queries → {HERE / 'queries.jsonl'}")
    print("query types: " + ", ".join(f"{t}={c}" for t, c in sorted(by_type.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
