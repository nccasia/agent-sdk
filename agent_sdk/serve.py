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
                "session_id": job.session.id if job.session else None,
            }
        )
        await self._conn().rpush(self._key, payload)
        return job.trace_id

    async def consume(self) -> AsyncIterator[Job]:
        while True:
            _, raw = await self._conn().blpop(self._key)
            data = json.loads(raw)
            sess = Session(data["session_id"]) if data.get("session_id") else None
            yield Job(input=data["input"], session=sess, trace_id=data["trace_id"])


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
    def __init__(
        self,
        agent: Any,
        *,
        queue: Queue,
        sink: EventSink,
        concurrency: int = 8,
        session_lock: Any | None = None,
    ):
        self.agent = agent
        self.queue = queue
        self.sink = sink
        self.concurrency = concurrency
        self.session_lock = session_lock or InProcessLock()

    def _lock_for(self, key: str):
        lock = self.session_lock(key) if callable(self.session_lock) else self.session_lock
        return lock

    async def _run_job(self, job: Job) -> None:
        key = job.session.id if job.session is not None else job.trace_id
        async with self._lock_for(key):
            async for ev in self.agent._run_stream(job.input, job.session):
                await self.sink.publish(job.trace_id, ev)
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
