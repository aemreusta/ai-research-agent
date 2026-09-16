"""The services a run uses, as protocols with a database and an in-memory implementation.

The agent core never imports SQLAlchemy. It talks to an `EventSink`, a `CallRecorder` and a
`Cache`; the agent service hands it the Postgres-backed versions, while the CLI and the scenario
tests hand it the in-memory ones. That is what lets `research run` and the test suite exercise
the exact code the service runs (architecture v0.6 §1).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from research_agent.db.models import EmbeddingCache, LlmCall, SearchCache, SearchCall
from research_agent.errors import AgentError
from research_agent.observability.events import EventLevel, EventType, new_span_id
from research_agent.observability.redaction import redact, redact_text

# --- events -------------------------------------------------------------------------------------


class EventSink(Protocol):
    run_id: uuid.UUID
    node: str
    iteration: int | None
    span_id: str | None

    def child(
        self, node: str, *, iteration: int | None = None, span_id: str | None = None
    ) -> EventSink: ...

    async def emit(
        self,
        event_type: EventType | str,
        message: str,
        *,
        level: EventLevel = EventLevel.INFO,
        data: dict[str, Any] | None = None,
        label: str | None = None,
        iteration: int | None = None,
        latency_ms: int | None = None,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
        cost_usd: float | None = None,
        error_code: str | None = None,
        expected: bool | None = None,
    ) -> int: ...

    async def debug(self, event_type: EventType | str, message: str, **kwargs: Any) -> int: ...
    async def info(self, event_type: EventType | str, message: str, **kwargs: Any) -> int: ...
    async def warn(self, event_type: EventType | str, message: str, **kwargs: Any) -> int: ...
    async def error(self, error: AgentError, **kwargs: Any) -> int: ...


@dataclass
class _Timeline:
    events: list[dict[str, Any]] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class MemoryEventSink:
    """Same behaviour as `EventWriter`, collected in a list (and optionally echoed)."""

    def __init__(
        self,
        run_id: uuid.UUID,
        *,
        node: str = "agent",
        iteration: int | None = None,
        span_id: str | None = None,
        parent_span_id: str | None = None,
        timeline: _Timeline | None = None,
        echo: Any = None,
    ) -> None:
        self.run_id = run_id
        self.node = node
        self.iteration = iteration
        self.span_id = span_id
        self.parent_span_id = parent_span_id
        self._timeline = timeline or _Timeline()
        self._echo = echo

    @property
    def events(self) -> list[dict[str, Any]]:
        return self._timeline.events

    def child(
        self, node: str, *, iteration: int | None = None, span_id: str | None = None
    ) -> MemoryEventSink:
        return MemoryEventSink(
            self.run_id,
            node=node,
            iteration=self.iteration if iteration is None else iteration,
            span_id=span_id or new_span_id(),
            parent_span_id=self.span_id,
            timeline=self._timeline,
            echo=self._echo,
        )

    async def emit(
        self,
        event_type: EventType | str,
        message: str,
        *,
        level: EventLevel = EventLevel.INFO,
        data: dict[str, Any] | None = None,
        label: str | None = None,
        iteration: int | None = None,
        latency_ms: int | None = None,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
        cost_usd: float | None = None,
        error_code: str | None = None,
        expected: bool | None = None,
    ) -> int:
        payload = dict(data or {})
        extra = {
            "latency_ms": latency_ms,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_usd": cost_usd,
            "error_code": error_code,
            "expected": expected,
        }
        if label:
            payload["display"] = f"[{label}] {message}"
        async with self._timeline.lock:
            seq = len(self._timeline.events) + 1
            event = {
                "seq": seq,
                "ts": datetime.now(UTC).isoformat(),
                "level": level.value,
                "node": self.node,
                "event_type": str(event_type),
                "message": redact_text(message),
                "data": redact(payload),
                "iteration": self.iteration if iteration is None else iteration,
                "span_id": self.span_id,
                "parent_span_id": self.parent_span_id,
                **{key: value for key, value in extra.items() if value is not None},
            }
            self._timeline.events.append(event)
        if self._echo is not None:
            self._echo(event)
        return seq

    async def debug(self, event_type: EventType | str, message: str, **kwargs: Any) -> int:
        return await self.emit(event_type, message, level=EventLevel.DEBUG, **kwargs)

    async def info(self, event_type: EventType | str, message: str, **kwargs: Any) -> int:
        return await self.emit(event_type, message, level=EventLevel.INFO, **kwargs)

    async def warn(self, event_type: EventType | str, message: str, **kwargs: Any) -> int:
        return await self.emit(event_type, message, level=EventLevel.WARN, **kwargs)

    async def error(self, error: AgentError, **kwargs: Any) -> int:
        message = f"{error.code.value}: {error.decision}"
        if error.outcome:
            message = f"{message} -> {error.outcome}"
        data = {
            "decision": error.decision,
            "outcome": error.outcome,
            "category": error.category.value,
            "provider": error.provider,
            "attempt": error.attempt,
            "cause": error.cause,
            **(kwargs.pop("data", None) or {}),
        }
        return await self.emit(
            EventType.ERROR,
            message,
            level=EventLevel.WARN if error.expected else EventLevel.ERROR,
            data=data,
            error_code=error.code.value,
            expected=error.expected,
            **kwargs,
        )

    def jsonl(self) -> str:
        return "".join(
            json.dumps(event, ensure_ascii=False, default=str) + "\n" for event in self.events
        )


# --- call records ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LlmCallRecord:
    run_id: uuid.UUID
    span_id: str
    node: str
    iteration: int | None
    provider: str
    model: str
    tier: str
    prompt_id: str | None
    prompt_version: str | None
    tokens_in: int
    tokens_out: int
    cost_usd: float
    latency_ms: int
    attempt: int
    fallback_from: str | None
    status: str
    error_code: str | None = None
    request: dict[str, Any] | None = None
    response: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class SearchCallRecord:
    run_id: uuid.UUID
    span_id: str
    node: str
    iteration: int | None
    provider: str
    query: str
    subquestion_id: str | None
    status: str
    result_count: int
    latency_ms: int
    cache_hit: bool
    error_code: str | None = None


class CallRecorder(Protocol):
    async def llm_call(self, record: LlmCallRecord) -> None: ...
    async def search_call(self, record: SearchCallRecord) -> None: ...


class MemoryCallRecorder:
    def __init__(self) -> None:
        self.llm_calls: list[LlmCallRecord] = []
        self.search_calls: list[SearchCallRecord] = []

    async def llm_call(self, record: LlmCallRecord) -> None:
        self.llm_calls.append(record)

    async def search_call(self, record: SearchCallRecord) -> None:
        self.search_calls.append(record)


class DbCallRecorder:
    """Writes `llm_calls` / `search_calls`. Payloads are redacted on the way in (boundary B2)."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def llm_call(self, record: LlmCallRecord) -> None:
        values = asdict(record)
        values["request_redacted"] = redact(values.pop("request"))
        values["response_redacted"] = redact(values.pop("response"))
        async with self._sessionmaker() as session:
            session.add(LlmCall(**values))
            await session.commit()

    async def search_call(self, record: SearchCallRecord) -> None:
        values = asdict(record)
        values["query"] = redact_text(values["query"])
        async with self._sessionmaker() as session:
            session.add(SearchCall(**values))
            await session.commit()


# --- caches ------------------------------------------------------------------------------------


def cache_key(*parts: Any) -> str:
    canonical = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


class Cache(Protocol):
    """Search/fetch responses (with TTL) and embeddings (without)."""

    async def get_response(self, key: str) -> dict[str, Any] | None: ...
    async def put_response(
        self, key: str, *, provider: str, query: str, response: dict[str, Any], ttl_seconds: int
    ) -> None: ...
    async def get_embeddings(self, keys: list[str]) -> dict[str, list[float]]: ...
    async def put_embeddings(self, model: str, vectors: dict[str, list[float]]) -> None: ...


class MemoryCache:
    def __init__(self) -> None:
        self._responses: dict[str, tuple[float, dict[str, Any]]] = {}
        self._embeddings: dict[str, list[float]] = {}

    async def get_response(self, key: str) -> dict[str, Any] | None:
        entry = self._responses.get(key)
        if entry is None or entry[0] < time.time():
            return None
        return entry[1]

    async def put_response(
        self, key: str, *, provider: str, query: str, response: dict[str, Any], ttl_seconds: int
    ) -> None:
        self._responses[key] = (time.time() + ttl_seconds, response)

    async def get_embeddings(self, keys: list[str]) -> dict[str, list[float]]:
        return {key: self._embeddings[key] for key in keys if key in self._embeddings}

    async def put_embeddings(self, model: str, vectors: dict[str, list[float]]) -> None:
        self._embeddings.update(vectors)


class DbCache:
    """`search_cache` and `embedding_cache`. A cache failure is never a run failure."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def get_response(self, key: str) -> dict[str, Any] | None:
        async with self._sessionmaker() as session:
            row = (
                await session.execute(
                    select(SearchCache.response).where(
                        SearchCache.key_hash == key,
                        SearchCache.expires_at > datetime.now(UTC),
                    )
                )
            ).scalar_one_or_none()
        return row

    async def put_response(
        self, key: str, *, provider: str, query: str, response: dict[str, Any], ttl_seconds: int
    ) -> None:
        expires = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
        statement = insert(SearchCache).values(
            key_hash=key,
            provider=provider,
            query=redact_text(query)[:2000],
            response=response,
            expires_at=expires,
        )
        statement = statement.on_conflict_do_update(
            index_elements=[SearchCache.key_hash],
            set_={"response": response, "expires_at": expires},
        )
        async with self._sessionmaker() as session:
            await session.execute(statement)
            await session.commit()

    async def get_embeddings(self, keys: list[str]) -> dict[str, list[float]]:
        if not keys:
            return {}
        async with self._sessionmaker() as session:
            rows = (
                await session.execute(
                    select(EmbeddingCache.key_hash, EmbeddingCache.vector).where(
                        EmbeddingCache.key_hash.in_(keys)
                    )
                )
            ).all()
        return {key: list(vector) for key, vector in rows}

    async def put_embeddings(self, model: str, vectors: dict[str, list[float]]) -> None:
        if not vectors:
            return
        rows = [
            {"key_hash": key, "model": model, "dimensions": len(vector), "vector": vector}
            for key, vector in vectors.items()
        ]
        statement = insert(EmbeddingCache).values(rows).on_conflict_do_nothing()
        async with self._sessionmaker() as session:
            await session.execute(statement)
            await session.commit()

    async def purge_expired(self) -> None:
        async with self._sessionmaker() as session:
            await session.execute(
                delete(SearchCache).where(SearchCache.expires_at < datetime.now(UTC))
            )
            await session.commit()
