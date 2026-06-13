#!/usr/bin/env python3
"""Generate a messy multi-party channel transcript + a long multi-step instruction.

The hardest memory scenario: an agent watching a busy chat channel — **hundreds of members talking
over each other for thousands of turns** — must (a) recognize the few **facts** buried in the chatter
and offload them to memory, (b) **list** what it knows, and (c) **recall** the *current* value of a
fact that was stated, then **updated**, hundreds of turns later — across the whole horizon.

Realism baked in:
* hundreds of speakers, interleaved topics, mostly low-signal chatter,
* facts stated naturally mid-conversation, tagged with ground truth (`fact_id`/`key`),
* **supersession across turns** — a deploy window / owner / config that changes at turn 100, then 900,
  then 1700; "current" ≠ the first-stated and ≠ a random later mention,
* a separate **long instruction** scenario: a multi-step task whose intermediate decisions/sub-goals
  must be offloaded (write-to-think) and recalled to finish.

Deterministic (seeded). Writes the committed source:
  dataset/channel.jsonl          turns       [{turn, speaker, text, fact_id?}]
  dataset/channel_facts.jsonl    ground truth[{fact_id, turn, key, entity, concept, value, supersedes}]
  dataset/channel_queries.jsonl  recall      [{id, type, query, expect_keys, note}]
  dataset/instruction.jsonl      one long multi-step task w/ offload + recall checkpoints

    python dataset/gen_channel.py --turns 1500 --members 200
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))

PROJECTS = ["zephyr", "atlas", "orion", "helios", "nova", "titan", "vega", "lyra", "draco"]
SERVICES = ["payments-api", "search", "auth", "gateway", "billing", "notifications", "ingest"]
# (entity, concept) tracks that get UPDATED over time → supersession across turns.
TRACKS = [
    ("deadline", lambda p, rng: f"the {p} deadline is now 2026-{rng.randint(1,12):02d}-{rng.randint(1,28):02d}"),
    ("schedule", lambda p, rng: f"{p} rollout is rescheduled to {rng.choice(['Mon','Wed','Fri'])} {rng.randint(9,18)}:00"),
    ("owner", lambda p, rng: f"{p} owner is now @user{rng.randint(1,200):03d}"),
    ("performance", lambda p, rng: f"{p} p99 latency is {rng.randint(40,900)}ms after the change"),
]
NOISE = [
    "lgtm 👍", "thanks!", "any updates on this?", "can someone take a look?", "+1", "ack",
    "I'll check after standup", "brb", "is the build green?", "who's on call tonight?",
    "merged", "reverted, was flaky", "let's sync tomorrow", "nice work team", "ship it",
    "the dashboard looks off", "re-running CI", "PTAL", "done", "following up here",
]


def _w(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")


def generate(turns: int, members: int, seed: int = 11):
    rng = random.Random(seed)
    transcript: list[dict] = []
    facts: list[dict] = []
    # current value per (entity, concept) so queries can assert the LATEST.
    latest: dict[tuple[str, str], str] = {}
    fid = 0

    def speaker():
        return f"@user{rng.randint(1, members):03d}"

    # state a fact roughly every ~5 turns; the rest is chatter.
    for t in range(turns):
        if rng.random() < 0.2:
            entity = rng.choice(PROJECTS + SERVICES)
            concept, tmpl = rng.choice(TRACKS)
            value = tmpl(entity, rng)
            key = f"{entity}-{concept}-t{t}"  # turn-stamped → versions accumulate (recency must win)
            prev = latest.get((entity, concept))
            facts.append({"fact_id": fid, "turn": t, "speaker": speaker(), "key": key,
                          "entity": entity, "concept": concept, "value": value, "supersedes": prev})
            latest[(entity, concept)] = key
            transcript.append({"turn": t, "speaker": speaker(), "text": value, "fact_id": fid})
            fid += 1
        else:
            who = speaker()
            txt = rng.choice(NOISE)
            if rng.random() < 0.3:  # a mention, still noise
                txt = f"{speaker()} {txt}"
            transcript.append({"turn": t, "speaker": who, "text": txt})

    # ── queries: recall the CURRENT value of a few tracked (entity, concept) pairs ──
    queries: list[dict] = []
    tracked = sorted(latest.keys())
    rng.shuffle(tracked)
    qword = {"deadline": "deadline", "schedule": "rollout schedule", "owner": "owner",
             "performance": "latency"}
    for i, (entity, concept) in enumerate(tracked[:12]):
        queries.append({
            "id": f"chan-{i}", "type": "current_value",
            "query": f"what is the current {qword[concept]} for {entity}",
            "expect_keys": [latest[(entity, concept)]],
            "note": "updated across turns — must return the LATEST, not a stale mention",
        })
    # a needle stated early and NEVER updated (search across the full horizon).
    needle_turn = max(1, turns // 50)
    facts.append({"fact_id": fid, "turn": needle_turn, "speaker": speaker(), "key": "incident-postmortem",
                  "entity": "payments-api", "concept": "incident",
                  "value": "postmortem: the Sev1 was caused by a connection pool leak in gateway",
                  "supersedes": None})
    transcript.insert(needle_turn, {"turn": needle_turn, "speaker": speaker(),
                                    "text": "postmortem: the Sev1 was caused by a connection pool leak in gateway",
                                    "fact_id": fid})
    queries.append({"id": "chan-needle", "type": "needle_at_distance",
                    "query": "what caused the payments sev1 outage", "expect_keys": ["incident-postmortem"],
                    "note": f"stated once at turn {needle_turn}, never repeated — find it across {turns} turns"})

    # ── a long multi-step instruction (write-to-think): offload, then recall to finish ──
    instruction = {
        "id": "instr-migration",
        "goal": ("Plan the zephyr→orion data migration. Work through it step by step; record each "
                 "decision and finding to memory as you go, then produce the final checklist from memory."),
        "steps": [
            {"step": 1, "do": "decide the cutover window", "offload": {"kind": "decision", "key": "cutover", "content": "cutover window: Saturday 02:00–04:00 UTC (low traffic)"}},
            {"step": 2, "do": "identify the blocking dependency", "offload": {"kind": "fact", "key": "blocker", "content": "blocker: the billing service must be drained before cutover"}},
            {"step": 3, "do": "assign the rollback owner", "offload": {"kind": "decision", "key": "rollback", "content": "rollback owner: @user042, trigger if error rate > 2%"}},
            {"step": 4, "do": "note the data-validation rule", "offload": {"kind": "fact", "key": "validation", "content": "validation: row counts must match within 0.01% post-migration"}},
            {"step": 5, "do": "record the comms plan", "offload": {"kind": "note", "key": "comms", "content": "comms: announce in #ops 24h before and at start"}},
        ],
        "final_recall": {"query": "summarize the migration plan",
                         "expect_keys": ["cutover", "blocker", "rollback", "validation", "comms"],
                         "note": "all 5 offloaded items must be recalled to produce the checklist"},
    }

    return transcript, facts, queries, [instruction]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--turns", type=int, default=1500)
    ap.add_argument("--members", type=int, default=200)
    ap.add_argument("--seed", type=int, default=11)
    args = ap.parse_args()
    transcript, facts, queries, instructions = generate(args.turns, args.members, seed=args.seed)
    _w(HERE / "channel.jsonl", transcript)
    _w(HERE / "channel_facts.jsonl", facts)
    _w(HERE / "channel_queries.jsonl", queries)
    _w(HERE / "instruction.jsonl", instructions)
    n_fact_turns = sum(1 for t in transcript if t.get("fact_id") is not None)
    print(f"wrote {len(transcript)} turns ({args.members} members, {n_fact_turns} fact-bearing, "
          f"{len(transcript)-n_fact_turns} noise) → channel.jsonl")
    print(f"wrote {len(facts)} ground-truth facts → channel_facts.jsonl")
    print(f"wrote {len(queries)} recall queries → channel_queries.jsonl")
    print(f"wrote {len(instructions)} long instruction → instruction.jsonl")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
