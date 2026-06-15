"""Serving at scale — a worker pool that drains a queue and streams over pub-sub.

``query()`` / ``act()`` are the in-process path. For production, run an
:class:`AgentWorker` pool that drains a :class:`Queue` and publishes events to an
:class:`EventSink`, with one in-flight turn per conversation enforced by a
session lock. ``InProcess*`` adapters need zero infra (dev); ``Redis*`` adapters
generalize the arq + Redis pub/sub + session-lock pattern the Mezon worker runs.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from agent_sdk.session import Session

__all__ = [
    "Job",
    "Queue",
    "EventSink",
    "InProcessQueue",
    "InProcessEventSink",
    "InProcessLock",
    "RedisQueue",
    "RedisEventSink",
    "RedisLock",
    "AgentWorker",
]

_DONE = object()


@dataclass
class Job:
    input: str
    session: Session | None = None
    # The conversation id the worker resolves against its ONE store (queue → session_id → load →
    # turn → save). Carried over the wire (a Session object isn't serializable); the worker binds
    # it to its store. ``session`` (a ready handle) takes precedence when both are set.
    session_id: str | None = None
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])


@runtime_checkable
class Queue(Protocol):
    async def enqueue(self, job: Job) -> str: ...
    def consume(self) -> AsyncIterator[Job]: ...


@runtime_checkable
class EventSink(Protocol):
    async def publish(self, trace_id: str, event: Any) -> None: ...
    async def close(self, trace_id: str) -> None: ...
    def subscribe(self, trace_id: str) -> AsyncIterator[Any]: ...


# ── in-process (dev / tests) ─────────────────────────────────────────────────
class InProcessQueue:
    def __init__(self) -> None:
        self._q: asyncio.Queue[Job] = asyncio.Queue()

    async def enqueue(self, job: Job) -> str:
        await self._q.put(job)
        return job.trace_id

    async def consume(self) -> AsyncIterator[Job]:
        while True:
            yield await self._q.get()


class InProcessEventSink:
    def __init__(self) -> None:
        self._subs: dict[str, asyncio.Queue] = {}

    def _q(self, trace_id: str) -> asyncio.Queue:
        return self._subs.setdefault(trace_id, asyncio.Queue())

    async def publish(self, trace_id: str, event: Any) -> None:
        await self._q(trace_id).put(event)

    async def close(self, trace_id: str) -> None:
        await self._q(trace_id).put(_DONE)

    async def subscribe(self, trace_id: str) -> AsyncIterator[Any]:
        q = self._q(trace_id)
        while True:
            ev = await q.get()
            if ev is _DONE:
                break
            yield ev


class InProcessLock:
    """One asyncio.Lock per key — call it with a key to get the lock."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    def __call__(self, key: str) -> asyncio.Lock:
        return self._locks.setdefault(key, asyncio.Lock())


# ── Redis (prod) ─────────────────────────────────────────────────────────────
class RedisQueue:
    def __init__(
        self, url: str | None = None, *, client: Any | None = None, key: str = "agent:jobs"
    ):
        self._url = url
        self._client = client
        self._key = key

    def _conn(self) -> Any:
        if self._client is None:
            import redis.asyncio as redis

            self._client = redis.from_url(self._url or "redis://localhost:6379")
        return self._client

    async def enqueue(self, job: Job) -> str:
        payload = json.dumps(
            {
                "input": job.input,
                "trace_id": job.trace_id,
                # Only the id crosses the wire — the worker binds it to its ONE store on consume.
                "session_id": job.session_id or (job.session.id if job.session else None),
            }
        )
        await self._conn().rpush(self._key, payload)
        return job.trace_id

    async def consume(self) -> AsyncIterator[Job]:
        while True:
            _, raw = await self._conn().blpop(self._key)
            data = json.loads(raw)
            yield Job(input=data["input"], session_id=data.get("session_id"),
                      trace_id=data["trace_id"])


class RedisEventSink:
    def __init__(
        self, url: str | None = None, *, client: Any | None = None, prefix: str = "agent:events:"
    ):
        self._url = url
        self._client = client
        self._prefix = prefix

    def _conn(self) -> Any:
        if self._client is None:
            import redis.asyncio as redis

            self._client = redis.from_url(self._url or "redis://localhost:6379")
        return self._client

    def _chan(self, trace_id: str) -> str:
        return f"{self._prefix}{trace_id}"

    async def publish(self, trace_id: str, event: Any) -> None:
        body = event.to_json() if hasattr(event, "to_json") else event
        await self._conn().publish(self._chan(trace_id), json.dumps(body))

    async def close(self, trace_id: str) -> None:
        await self._conn().publish(self._chan(trace_id), json.dumps({"type": "_done"}))

    async def subscribe(self, trace_id: str) -> AsyncIterator[Any]:
        pubsub = self._conn().pubsub()
        await pubsub.subscribe(self._chan(trace_id))
        async for msg in pubsub.listen():
            if msg.get("type") != "message":
                continue
            data = json.loads(msg["data"])
            if data.get("type") == "_done":
                break
            yield data


class RedisLock:
    """A per-key distributed lock (SET NX PX + spin)."""

    def __init__(
        self,
        url: str | None = None,
        *,
        client: Any | None = None,
        ttl_ms: int = 30_000,
        prefix: str = "agent:lock:",
    ):
        self._url = url
        self._client = client
        self._ttl = ttl_ms
        self._prefix = prefix

    def _conn(self) -> Any:
        if self._client is None:
            import redis.asyncio as redis

            self._client = redis.from_url(self._url or "redis://localhost:6379")
        return self._client

    @asynccontextmanager
    async def __call__(self, key: str):  # type: ignore[override]
        token = uuid.uuid4().hex
        rkey = f"{self._prefix}{key}"
        conn = self._conn()
        # Distributed spinlock — a process-local asyncio.Event can't coordinate
        # across workers, so polling SET NX is the correct cross-process pattern.
        while not await conn.set(rkey, token, nx=True, px=self._ttl):  # noqa: ASYNC110
            await asyncio.sleep(0.02)
        try:
            yield
        finally:
            if await conn.get(rkey) in (token, token.encode()):
                await conn.delete(rkey)


# ── the worker pool ──────────────────────────────────────────────────────────
class AgentWorker:
    """An effective queue worker: ``request → find session id → load → turn → respond → offload``.

    Holds ONE ``store`` and the (immutable) agent config; everything per-session lives in the
    store as one JSON snapshot (``SessionState.to_json`` — conversation + universal memory). Each
    job names a ``session_id``; the worker binds it to the store, the turn loads/runs/saves that
    one blob natively (no separate memory write). So the process is stateless across sessions —
    any replica serves any session and a restart loses nothing.

    Concurrency: pass ``agent_factory`` to build a pool of ``concurrency`` agents — each in-flight
    turn checks one out exclusively, so two sessions never interleave on a shared working-memory
    store. A single shared ``agent`` also works (turns serialize through it). A job may instead
    carry a ready ``Session`` (it wins over ``session_id``), e.g. for the in-process path."""

    def __init__(
        self,
        agent: Any | None = None,
        *,
        agent_factory: Any | None = None,
        queue: Queue,
        sink: EventSink,
        store: Any | None = None,
        concurrency: int = 8,
        session_lock: Any | None = None,
    ):
        if agent is None and agent_factory is None:
            raise ValueError("AgentWorker needs an agent or an agent_factory")
        self.agent = agent
        self._factory = agent_factory
        self.queue = queue
        self.sink = sink
        self.store = store
        self.concurrency = concurrency
        self.session_lock = session_lock or InProcessLock()
        self._pool: asyncio.Queue | None = None

    def _session_for(self, job: Job) -> Session | None:
        """Resolve the job's session: a ready handle wins; else bind its id to the one store."""
        if job.session is not None:
            return job.session
        if job.session_id is not None and self.store is not None:
            return Session(job.session_id, self.store)
        if job.session_id is not None:
            return Session(job.session_id)  # zero-infra default store
        return None

    def _ensure_pool(self) -> asyncio.Queue:
        if self._pool is None:
            self._pool = asyncio.Queue()
            if self._factory is not None:
                for _ in range(max(1, self.concurrency)):
                    self._pool.put_nowait(self._factory())
            else:
                # One shared agent → checkout serializes turns through it (no memory race).
                self._pool.put_nowait(self.agent)
        return self._pool

    def _lock_for(self, key: str):
        lock = self.session_lock(key) if callable(self.session_lock) else self.session_lock
        return lock

    async def _run_job(self, job: Job) -> None:
        pool = self._ensure_pool()
        session = self._session_for(job)  # find session id → load from the one store
        key = session.id if session is not None else job.trace_id
        async with self._lock_for(key):  # one in-flight turn per conversation
            agent = await pool.get()  # exclusive for this turn → no cross-session memory race
            try:
                # A pooled agent is reused across sessions (and thus across bots/tenants). When a
                # session IS present, _run_stream resets+restores its working memory from that
                # session's snapshot — so it can never carry another session's memory. A
                # SESSIONLESS job has no snapshot to restore, so reset explicitly here: a pooled
                # agent must never inherit the previous job's memory. (The in-process query() path
                # has no pool and intentionally keeps accumulating.)
                if session is None:
                    mem = getattr(agent, "_memory_store", None)
                    if mem is not None:
                        mem.reset()
                # _run_stream loads the snapshot, runs the turn, and offloads (saves the whole
                # state back to the store) — the native load→turn→save cycle, no extra write.
                async for ev in agent._run_stream(job.input, session):
                    await self.sink.publish(job.trace_id, ev)
            finally:
                pool.put_nowait(agent)
        await self.sink.close(job.trace_id)

    async def serve(self, *, max_jobs: int | None = None) -> None:
        """Drain → run → publish. ``max_jobs`` bounds the loop (tests); omit to
        run until cancelled (production)."""
        sem = asyncio.Semaphore(self.concurrency)
        tasks: list[asyncio.Future] = []
        processed = 0

        async def handle(job: Job) -> None:
            try:
                await self._run_job(job)
            finally:
                sem.release()

        async for job in self.queue.consume():
            await sem.acquire()
            tasks.append(asyncio.ensure_future(handle(job)))
            processed += 1
            if max_jobs is not None and processed >= max_jobs:
                break
        if tasks:
            await asyncio.gather(*tasks)
