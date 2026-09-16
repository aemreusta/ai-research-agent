"""The user-facing API. Every test here is something the browser depends on."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from research_agent.api.app import create_api_app
from research_agent.contracts import RunStatus
from research_agent.db.models import RunSecret
from research_agent.db.repository import RunRepository
from research_agent.db.session import libpq_dsn
from research_agent.keys import SecretBox
from research_agent.observability.events import EventType, EventWriter

pytestmark = pytest.mark.integration


@pytest.fixture
async def client(
    migrated_database: str, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_api_app(
        sessionmaker=db_sessionmaker,
        secret_box=SecretBox(Fernet.generate_key().decode()),
        dsn=libpq_dsn(migrated_database),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api") as http:
        yield http


# --- health ------------------------------------------------------------------


async def test_healthz_does_not_depend_on_anything(client: httpx.AsyncClient) -> None:
    """Liveness must not fail because Postgres blinked - that is what readyz is for."""
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_readyz_reports_each_dependency(client: httpx.AsyncClient) -> None:
    body = (await client.get("/readyz")).json()
    assert body["checks"]["database"]["ok"] is True
    assert "presidio" in body["checks"]


# --- creating runs -----------------------------------------------------------


async def test_creating_a_run_queues_it(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    response = await client.post("/api/runs", json={"question": "What changed in the EU AI Act?"})
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == RunStatus.QUEUED.value
    assert body["config_hash"]

    run = await RunRepository(db_session).require(uuid.UUID(body["run_id"]))
    assert run.status == RunStatus.QUEUED.value
    assert run.config_snapshot["budget"]["max_iterations"] == 4


async def test_the_question_is_masked_before_it_is_stored(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Boundary B1: unmasked identifiers never reach the database (v0.6 §12)."""
    response = await client.post(
        "/api/runs", json={"question": "Müvekkilim 10000000146 için KVKK riski nedir?"}
    )
    run_id = uuid.UUID(response.json()["run_id"])

    run = await RunRepository(db_session).require(run_id)
    assert "10000000146" not in run.question_masked
    assert "<TCKN_1>" in run.question_masked
    assert response.json()["pii_masked"] == {"TCKN": 1}


async def test_an_empty_question_is_rejected(client: httpx.AsyncClient) -> None:
    assert (await client.post("/api/runs", json={"question": "   "})).status_code == 422


async def test_an_absurdly_long_question_is_rejected(client: httpx.AsyncClient) -> None:
    assert (await client.post("/api/runs", json={"question": "x" * 5000})).status_code == 422


async def test_a_valid_override_is_recorded_with_its_provenance(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    response = await client.post(
        "/api/runs",
        json={"question": "Market size?", "overrides": {"budget.max_searches": 12}},
    )
    assert response.status_code == 201
    run = await RunRepository(db_session).require(uuid.UUID(response.json()["run_id"]))
    assert run.config_snapshot["budget"]["max_searches"] == 12
    assert run.overrides == {"budget.max_searches": 12}


async def test_an_out_of_bounds_override_is_refused_before_the_run_starts(
    client: httpx.AsyncClient,
) -> None:
    """The browser renders the bounds; the server does not trust it (D24)."""
    response = await client.post(
        "/api/runs",
        json={"question": "Market size?", "overrides": {"budget.max_searches": 99999}},
    )
    assert response.status_code == 400
    assert response.json()["error_code"] == "CONFIG_INVALID"


async def test_a_locked_setting_cannot_be_overridden(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/runs", json={"question": "Market size?", "overrides": {"gate.enabled": False}}
    )
    assert response.status_code == 400
    assert "security-critical" in response.json()["message"]


async def test_supplied_keys_are_stored_encrypted_and_never_echoed(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    response = await client.post(
        "/api/runs",
        json={"question": "Market size?", "keys": {"tavily": "tvly-abcdefghijklmnop"}},
    )
    assert response.status_code == 201
    assert "tvly-abcdefghijklmnop" not in response.text
    assert response.json()["key_sources"] == {"tavily": "ui"}

    secret = (await db_session.execute(select(RunSecret))).scalars().one()
    assert "tvly-abcdefghijklmnop" not in secret.ciphertext


# --- reading runs ------------------------------------------------------------


async def test_listing_runs_is_newest_first(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    for question in ("first", "second", "third"):
        await client.post("/api/runs", json={"question": question})
    body = (await client.get("/api/runs")).json()
    assert [item["question"] for item in body["runs"]] == ["third", "second", "first"]
    assert body["total"] == 3


async def test_fetching_one_run_includes_its_report_when_there_is_one(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    created = (await client.post("/api/runs", json={"question": "Market size?"})).json()
    run_id = created["run_id"]

    repo = RunRepository(db_session)
    await repo.claim(dispatcher_id="d1")
    await repo.mark_running(uuid.UUID(run_id), agent_id="a1", deadline_at=None)
    await repo.finish(
        uuid.UUID(run_id), status=RunStatus.SUCCEEDED, stop_reason="sufficient", gate_status="pass"
    )

    body = (await client.get(f"/api/runs/{run_id}")).json()
    assert body["status"] == "succeeded"
    assert body["stop_reason"] == "sufficient"
    assert body["gate_status"] == "pass"


async def test_an_unknown_run_is_404(client: httpx.AsyncClient) -> None:
    assert (await client.get(f"/api/runs/{uuid.uuid4()}")).status_code == 404


async def test_a_run_response_never_carries_key_material(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    created = (
        await client.post(
            "/api/runs",
            json={"question": "Market size?", "keys": {"openai": "sk-abcdefghijklmnopqrst"}},
        )
    ).json()
    body = (await client.get(f"/api/runs/{created['run_id']}")).text
    assert "sk-abcdefghijklmnopqrst" not in body


# --- cancel ------------------------------------------------------------------


async def test_cancelling_a_queued_run_settles_it_immediately(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Nobody is executing it, so there is nothing to ask politely."""
    created = (await client.post("/api/runs", json={"question": "Market size?"})).json()
    response = await client.post(f"/api/runs/{created['run_id']}/cancel")
    assert response.status_code == 202

    run = await RunRepository(db_session).require(uuid.UUID(created["run_id"]))
    assert run.status == RunStatus.CANCELLED.value


async def test_cancelling_a_running_run_sets_the_flag(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    created = (await client.post("/api/runs", json={"question": "Market size?"})).json()
    run_id = uuid.UUID(created["run_id"])
    repo = RunRepository(db_session)
    await repo.claim(dispatcher_id="d1")
    await repo.mark_running(run_id, agent_id="a1", deadline_at=None)

    assert (await client.post(f"/api/runs/{run_id}/cancel")).status_code == 202
    run = await repo.require(run_id)
    assert run.cancel_requested is True
    assert run.status == RunStatus.RUNNING.value, "the agent writes the terminal status"


async def test_cancelling_a_finished_run_is_a_conflict(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    created = (await client.post("/api/runs", json={"question": "Market size?"})).json()
    run_id = uuid.UUID(created["run_id"])
    repo = RunRepository(db_session)
    await repo.claim(dispatcher_id="d1")
    await repo.mark_running(run_id, agent_id="a1", deadline_at=None)
    await repo.finish(run_id, status=RunStatus.SUCCEEDED)

    assert (await client.post(f"/api/runs/{run_id}/cancel")).status_code == 409


# --- events / SSE ------------------------------------------------------------


async def test_events_are_backfilled_from_the_database(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """A browser opening a finished run must still see the whole timeline."""
    created = (await client.post("/api/runs", json={"question": "Market size?"})).json()
    run_id = uuid.UUID(created["run_id"])
    writer = EventWriter(db_sessionmaker, run_id=run_id, node="plan")
    await writer.info(EventType.PLAN_CREATED, "Created 4 research tasks.", label="Planner")
    repo = RunRepository(db_session)
    await repo.claim(dispatcher_id="d1")
    await repo.mark_running(run_id, agent_id="a1", deadline_at=None)
    await repo.finish(run_id, status=RunStatus.SUCCEEDED)

    events = [event async for event in _read_sse(client, f"/api/runs/{run_id}/events", expect=1)]
    assert events[0]["event_type"] == "plan_created"
    assert events[0]["data"]["display"] == "[Planner] Created 4 research tasks."


async def test_a_reconnecting_client_resumes_after_its_last_event(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """`Last-Event-ID` is why an SSE drop does not replay the whole run (v0.6 §15.1)."""
    created = (await client.post("/api/runs", json={"question": "Market size?"})).json()
    run_id = uuid.UUID(created["run_id"])
    writer = EventWriter(db_sessionmaker, run_id=run_id, node="plan")
    for index in range(3):
        await writer.info(EventType.NODE_FINISHED, f"step {index}")
    repo = RunRepository(db_session)
    await repo.claim(dispatcher_id="d1")
    await repo.mark_running(run_id, agent_id="a1", deadline_at=None)
    await repo.finish(run_id, status=RunStatus.SUCCEEDED)

    events = [
        event
        async for event in _read_sse(
            client, f"/api/runs/{run_id}/events", headers={"Last-Event-ID": "2"}, expect=1
        )
    ]
    assert [event["seq"] for event in events] == [3]


async def test_live_events_arrive_while_a_run_executes(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    created = (await client.post("/api/runs", json={"question": "Market size?"})).json()
    run_id = uuid.UUID(created["run_id"])
    repo = RunRepository(db_session)
    await repo.claim(dispatcher_id="d1")
    await repo.mark_running(run_id, agent_id="a1", deadline_at=None)
    writer = EventWriter(db_sessionmaker, run_id=run_id, node="search")

    async def emit_later() -> None:
        await asyncio.sleep(0.2)
        await writer.info(EventType.SEARCH_CALLED, "Received 8 results.", label="Search")
        await repo.finish(run_id, status=RunStatus.SUCCEEDED)

    task = asyncio.create_task(emit_later())
    events = [event async for event in _read_sse(client, f"/api/runs/{run_id}/events", expect=1)]
    await task
    assert events[0]["message"] == "Received 8 results."


# --- config schema, presets, export -----------------------------------------


async def test_the_config_schema_drives_the_advanced_form(client: httpx.AsyncClient) -> None:
    body = (await client.get("/api/config/schema")).json()
    paths = {field["path"] for field in body["fields"]}
    assert "budget.max_searches" in paths
    assert all(field["group"] and field["description"] for field in body["fields"])


async def test_locked_settings_are_absent_from_the_schema(client: httpx.AsyncClient) -> None:
    """The UI cannot render what it is not told about, and the server refuses it anyway."""
    body = (await client.get("/api/config/schema")).json()
    paths = {field["path"] for field in body["fields"]}
    assert "gate.enabled" not in paths
    assert "pii.enabled" not in paths


async def test_presets_round_trip(client: httpx.AsyncClient) -> None:
    created = await client.post(
        "/api/presets",
        json={"name": "cheap", "overrides": {"budget.max_searches": 10}},
    )
    assert created.status_code == 201
    listed = (await client.get("/api/presets")).json()
    assert [preset["name"] for preset in listed["presets"]] == ["cheap"]


async def test_a_preset_with_an_invalid_override_is_refused(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/presets", json={"name": "broken", "overrides": {"budget.max_searches": -1}}
    )
    assert response.status_code == 400


async def test_a_duplicate_preset_name_is_a_conflict(client: httpx.AsyncClient) -> None:
    payload = {"name": "same", "overrides": {"budget.max_searches": 10}}
    await client.post("/api/presets", json=payload)
    assert (await client.post("/api/presets", json=payload)).status_code == 409


async def test_export_returns_the_report_and_the_trace(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    created = (await client.post("/api/runs", json={"question": "Market size?"})).json()
    run_id = uuid.UUID(created["run_id"])
    writer = EventWriter(db_sessionmaker, run_id=run_id, node="plan")
    await writer.info(EventType.PLAN_CREATED, "Created 4 research tasks.")

    trace = await client.get(f"/api/runs/{run_id}/export?artifact=trace")
    assert trace.status_code == 200
    lines = [json.loads(line) for line in trace.text.strip().splitlines()]
    assert lines[0]["event_type"] == "plan_created"


async def test_the_ui_is_served_at_the_root(client: httpx.AsyncClient) -> None:
    response = await client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


# --- helpers -----------------------------------------------------------------


async def _read_sse(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    expect: int,
) -> AsyncIterator[dict[str, Any]]:
    """Read `expect` data frames off an SSE stream, then stop."""
    received = 0
    async with client.stream("GET", url, headers=headers, timeout=5.0) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        async for line in response.aiter_lines():
            if not line.startswith("data:"):
                continue
            payload = json.loads(line.removeprefix("data:").strip())
            if payload.get("event_type") == "__keepalive__":
                continue
            yield payload
            received += 1
            if received >= expect:
                return
