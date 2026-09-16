"""FastAPI application for the agent replica.

The routes mirror `contracts/agent-api.openapi.yaml` exactly, and a contract test compares the
schema FastAPI generates against that file. Error bodies use the shared `error_codes.yaml`
vocabulary so the Go client can branch on a code rather than parse a message.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated, Literal

from fastapi import FastAPI, Path, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from research_agent import __version__
from research_agent.agent_server.executor import (
    AlreadyExecutingError,
    NoCapacityError,
    RunExecutor,
)
from research_agent.db.repository import RunNotFoundError
from research_agent.errors import AgentException


class ExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt: int = Field(ge=1, description="Above 1 the agent resumes from its checkpoint.")
    deadline_at: datetime | None = Field(
        default=None, description="For visibility; the dispatcher enforces it."
    )
    dispatcher_id: str | None = Field(default=None, max_length=128)


class ExecuteAccepted(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accepted: Literal[True] = True
    run_id: uuid.UUID
    agent_id: str


class CancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=256)


class CancelAccepted(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accepted: Literal[True] = True
    run_id: uuid.UUID
    was_running: bool


class Health(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "draining"]
    agent_id: str
    version: str


class Capacity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str
    slots_total: int
    slots_used: int
    slots_free: int
    running_run_ids: list[uuid.UUID] = Field(default_factory=list)


class ErrorBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error_code: str
    message: str
    expected: bool


def _error(exc: AgentException, http_status: int) -> JSONResponse:
    error = exc.error
    return JSONResponse(
        status_code=http_status,
        content=ErrorBody(
            error_code=error.code.value,
            message=error.outcome or error.decision,
            expected=error.expected,
        ).model_dump(),
    )


def agent_id_from_environment() -> str:
    """The container id is the natural replica identity under `--scale agent=N`."""
    return os.environ.get("AGENT_ID") or os.environ.get("HOSTNAME") or "agent-local"


def create_agent_app(executor: RunExecutor) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        # SIGTERM: stop accepting work, let in-flight runs finish. Anything still running when
        # the grace period ends is recovered by the watchdog from its checkpoint.
        await executor.drain()

    app = FastAPI(
        title="Research Agent - agent API",
        version=__version__,
        description="Control-plane to data-plane calls only (contracts/agent-api.openapi.yaml).",
        lifespan=lifespan,
    )

    @app.get("/healthz", response_model=Health, responses={503: {"model": Health}})
    async def healthz(response: Response) -> Health:
        health = Health.model_validate(executor.health())
        if executor.draining:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return health

    @app.get("/capacity", response_model=Capacity)
    async def capacity() -> Capacity:
        return Capacity.model_validate(executor.capacity())

    @app.post(
        "/v1/runs/{run_id}/execute",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=ExecuteAccepted,
        responses={
            404: {"model": ErrorBody},
            409: {"model": ErrorBody},
            503: {"model": ErrorBody},
        },
    )
    async def execute_run(
        run_id: Annotated[uuid.UUID, Path()], request: ExecuteRequest
    ) -> ExecuteAccepted | JSONResponse:
        try:
            await executor.execute(
                run_id,
                attempt=request.attempt,
                deadline_at=request.deadline_at,
                dispatcher_id=request.dispatcher_id,
            )
        except AlreadyExecutingError as exc:
            return _error(exc, status.HTTP_409_CONFLICT)
        except NoCapacityError as exc:
            return _error(exc, status.HTTP_503_SERVICE_UNAVAILABLE)
        except RunNotFoundError as exc:
            return _error(exc, status.HTTP_404_NOT_FOUND)
        return ExecuteAccepted(run_id=run_id, agent_id=executor.agent_id)

    @app.post(
        "/v1/runs/{run_id}/cancel",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=CancelAccepted,
        responses={404: {"model": ErrorBody}},
    )
    async def cancel_run(
        run_id: Annotated[uuid.UUID, Path()], request: CancelRequest | None = None
    ) -> CancelAccepted | JSONResponse:
        try:
            was_running = await executor.cancel(run_id, reason=request.reason if request else None)
        except RunNotFoundError as exc:
            return _error(exc, status.HTTP_404_NOT_FOUND)
        return CancelAccepted(run_id=run_id, was_running=was_running)

    return app
