"""Creating, reading, cancelling, streaming and exporting runs."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Header, Path, Query, Request, Response, status
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from sqlalchemy import func, select

from research_agent.api.deps import Api
from research_agent.api.schemas import (
    CancelResponse,
    CreateRunRequest,
    CreateRunResponse,
    ErrorResponse,
    RunDetail,
    RunList,
    RunSummary,
)
from research_agent.api.sse import stream_events
from research_agent.config.loader import ConfigError, load_settings
from research_agent.contracts import RunStatus, run_state_machine
from research_agent.db.models import Preset, Run, RunArtifact, RunEvent
from research_agent.db.repository import RunRepository
from research_agent.errors import AgentException
from research_agent.keys import Provider, ProviderKeys

router = APIRouter(prefix="/api/runs", tags=["runs"])


def _error(exc: AgentException, http_status: int) -> JSONResponse:
    error = exc.error
    return JSONResponse(
        status_code=http_status,
        content=ErrorResponse(
            error_code=error.code.value,
            message=error.outcome or error.cause or error.decision,
            expected=error.expected,
        ).model_dump(),
    )


def _duration(run: Run) -> float | None:
    end: datetime | None = run.finished_at
    start: datetime | None = run.started_at or run.created_at
    if end is None or start is None:
        return None
    return (end - start).total_seconds()


def _summary(run: Run) -> RunSummary:
    return RunSummary(
        run_id=run.id,
        status=run.status,
        question=run.question_masked,
        stop_reason=run.stop_reason,
        gate_status=run.gate_status,
        error_code=run.error_code,
        iteration=run.iteration,
        searches_used=run.searches_used,
        tokens_in=run.tokens_in,
        tokens_out=run.tokens_out,
        cost_usd=float(run.cost_usd),
        created_at=run.created_at,
        finished_at=run.finished_at,
        duration_seconds=_duration(run),
    )


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=CreateRunResponse,
    responses={400: {"model": ErrorResponse}},
)
async def create_run(request: CreateRunRequest, api: Api) -> CreateRunResponse | JSONResponse:
    """Validate, mask, encrypt, enqueue - in that order.

    Configuration is resolved and hashed here rather than in the agent so that an invalid
    override is rejected before a run exists at all (`CONFIG_INVALID`, v0.6 §13.2), and so the
    run carries the exact settings it will be executed with even if `config/` changes later.
    """
    async with api.sessionmaker() as session:
        overrides = dict(request.overrides)
        if request.preset:
            stored = (
                await session.execute(select(Preset).where(Preset.name == request.preset))
            ).scalar_one_or_none()
            if stored is not None:
                # Explicit overrides win over the preset they were layered on.
                overrides = {**stored.overrides, **overrides}

        try:
            config = load_settings(overrides=overrides)
        except ConfigError as exc:
            return _error(exc, status.HTTP_400_BAD_REQUEST)

        # Boundary B1: mask before anything is written or sent anywhere (v0.6 §12).
        masked = api.masker.mask(request.question)

        known = {provider.value for provider in Provider}
        supplied = {Provider(name): value for name, value in request.keys.items() if name in known}
        keys = ProviderKeys.resolve(supplied)

        repository = RunRepository(session)
        run = await repository.create(
            question_masked=masked.text,
            config_snapshot=config.snapshot,
            config_hash=config.config_hash,
            overrides=overrides,
            key_sources=keys.sources(),
        )
        if api.secret_box is not None:
            await repository.store_secrets(run.id, keys.encrypted(api.secret_box))

        return CreateRunResponse(
            run_id=run.id,
            status=run.status,
            config_hash=run.config_hash,
            key_sources=keys.sources(),
            pii_masked=masked.summary(),
        )


@router.get("", response_model=RunList)
async def list_runs(
    api: Api,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> RunList:
    async with api.sessionmaker() as session:
        runs = await RunRepository(session).list_recent(limit=limit, offset=offset)
        total = (await session.execute(select(func.count()).select_from(Run))).scalar_one()
    return RunList(runs=[_summary(run) for run in runs], total=int(total))


@router.get("/{run_id}", response_model=RunDetail, responses={404: {"model": ErrorResponse}})
async def get_run(run_id: Annotated[uuid.UUID, Path()], api: Api) -> RunDetail | JSONResponse:
    async with api.sessionmaker() as session:
        run = await RunRepository(session).get(run_id)
        if run is None:
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content=ErrorResponse(
                    error_code="NOT_FOUND", message="no such run", expected=True
                ).model_dump(),
            )
        artifacts = (
            (await session.execute(select(RunArtifact).where(RunArtifact.run_id == run_id)))
            .scalars()
            .all()
        )

    stored = {artifact.kind: artifact.content for artifact in artifacts}
    gate_result: dict[str, Any] | None = None
    if raw := stored.get("gate_result"):
        gate_result = json.loads(raw)

    return RunDetail(
        **_summary(run).model_dump(),
        config_hash=run.config_hash,
        config_snapshot=run.config_snapshot,
        overrides=run.overrides,
        key_sources=run.key_sources,
        models_used=run.models_used,
        prompt_versions=run.prompt_versions,
        skills_used=run.skills_used,
        attempts=run.attempts,
        agent_id=run.agent_id,
        cancel_requested=run.cancel_requested,
        langfuse_trace_url=run.langfuse_trace_url,
        error_message=run.error_message,
        artifacts=sorted(stored),
        report_md=stored.get("report_md"),
        gate_result=gate_result,
    )


@router.post(
    "/{run_id}/cancel",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=CancelResponse,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
async def cancel_run(
    run_id: Annotated[uuid.UUID, Path()], api: Api
) -> CancelResponse | JSONResponse:
    """Set the flag; settle the run here only if no agent can settle it.

    A running run is stopped by its own agent at the next node boundary, so the terminal write
    stays with the one process that knows what has been persisted.
    """
    async with api.sessionmaker() as session:
        repository = RunRepository(session)
        run = await repository.get(run_id)
        if run is None:
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content=ErrorResponse(
                    error_code="NOT_FOUND", message="no such run", expected=True
                ).model_dump(),
            )
        current = RunStatus(run.status)
        if run_state_machine().is_terminal(current):
            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content=ErrorResponse(
                    error_code="ALREADY_FINISHED",
                    message=f"run is already {current.value}",
                    expected=True,
                ).model_dump(),
            )

        await repository.request_cancel(run_id)
        if current is RunStatus.QUEUED:
            run = await repository.finish(
                run_id, status=RunStatus.CANCELLED, stop_reason="cancelled"
            )
        else:
            run = await repository.require(run_id)

    return CancelResponse(run_id=run_id, status=run.status, cancel_requested=True)


@router.get("/{run_id}/events")
async def run_events(
    run_id: Annotated[uuid.UUID, Path()],
    request: Request,
    api: Api,
    last_event_id: Annotated[int | None, Query(ge=0)] = None,
    header_last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    """SSE. `EventSource` sends `Last-Event-ID` itself; the query parameter is for curl."""
    resume_from = last_event_id or 0
    if header_last_event_id and header_last_event_id.isdigit():
        resume_from = max(resume_from, int(header_last_event_id))

    return StreamingResponse(
        stream_events(api.sessionmaker, run_id=run_id, last_event_id=resume_from, dsn=api.dsn),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Nginx and friends buffer streamed responses by default, which would make the live
            # timeline arrive all at once at the end.
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/{run_id}/export", responses={404: {"model": ErrorResponse}})
async def export_run(
    run_id: Annotated[uuid.UUID, Path()],
    api: Api,
    artifact: Annotated[str, Query()] = "report",
) -> Response:
    """`report` (markdown), `trace` (jsonl), `state` or `gate` - the same files as `examples/`."""
    async with api.sessionmaker() as session:
        run = await RunRepository(session).get(run_id)
        if run is None:
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content=ErrorResponse(
                    error_code="NOT_FOUND", message="no such run", expected=True
                ).model_dump(),
            )
        if artifact == "trace":
            events = (
                (
                    await session.execute(
                        select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.seq)
                    )
                )
                .scalars()
                .all()
            )
            lines = "\n".join(
                json.dumps(
                    {
                        "seq": event.seq,
                        "ts": event.ts.isoformat(),
                        "level": event.level,
                        "node": event.node,
                        "event_type": event.event_type,
                        "message": event.message,
                        "data": event.data,
                        "iteration": event.iteration,
                        "span_id": event.span_id,
                        "parent_span_id": event.parent_span_id,
                        "error_code": event.error_code,
                    },
                    ensure_ascii=False,
                    default=str,
                )
                for event in events
            )
            return PlainTextResponse(
                lines + "\n" if lines else "",
                media_type="application/x-ndjson",
                headers={"Content-Disposition": f'attachment; filename="trace-{run_id}.jsonl"'},
            )

        kinds = {"report": "report_md", "state": "state", "gate": "gate_result"}
        kind = kinds.get(artifact)
        if kind is None:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content=ErrorResponse(
                    error_code="CONFIG_INVALID",
                    message=f"unknown artifact '{artifact}'; try {[*sorted(kinds), 'trace']}",
                    expected=True,
                ).model_dump(),
            )
        stored = (
            await session.execute(
                select(RunArtifact).where(RunArtifact.run_id == run_id, RunArtifact.kind == kind)
            )
        ).scalar_one_or_none()

    if stored is None:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content=ErrorResponse(
                error_code="NOT_FOUND",
                message=f"this run has no {artifact} yet",
                expected=True,
            ).model_dump(),
        )
    extension = "md" if kind == "report_md" else "json"
    return PlainTextResponse(
        stored.content,
        media_type=stored.content_type,
        headers={"Content-Disposition": f'attachment; filename="{artifact}-{run_id}.{extension}"'},
    )
