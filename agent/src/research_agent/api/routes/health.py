"""Liveness and readiness.

`/healthz` answers "is this process running" and deliberately touches nothing, so a database
hiccup does not get the container restarted. `/readyz` is the diagnostic a reviewer actually
wants after `docker compose up`: it names every dependency, says whether it answered, and marks
which ones the system genuinely cannot work without.
"""

from __future__ import annotations

import os

import httpx
from fastapi import APIRouter, Response, status
from sqlalchemy import text

from research_agent import __version__
from research_agent.api.deps import Api, ApiState
from research_agent.api.schemas import DependencyCheck, HealthResponse, ReadyResponse

router = APIRouter(tags=["health"])

_PROBE_TIMEOUT_SECONDS = 2.0


@router.get("/healthz", response_model=HealthResponse)
async def healthz() -> HealthResponse:
    return HealthResponse(status="ok", service="api", version=__version__)


async def _check_database(api: ApiState) -> DependencyCheck:
    try:
        async with api.sessionmaker() as session:
            await session.execute(text("SELECT 1"))
        return DependencyCheck(ok=True, detail="reachable", required=True)
    except Exception as exc:
        return DependencyCheck(ok=False, detail=type(exc).__name__, required=True)


async def _check_http(url: str | None, *, required: bool) -> DependencyCheck:
    if not url:
        return DependencyCheck(ok=False, detail="not configured", required=required)
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_SECONDS) as client:
            response = await client.get(url)
        return DependencyCheck(ok=response.status_code < 500, detail=url, required=required)
    except Exception as exc:
        return DependencyCheck(ok=False, detail=f"{type(exc).__name__}: {url}", required=required)


@router.get("/readyz", response_model=ReadyResponse)
async def readyz(response: Response, api: Api) -> ReadyResponse:
    analyzer = os.environ.get("PRESIDIO_ANALYZER_URL")
    checks = {
        "database": await _check_database(api),
        # Presidio is not required: without it intake falls back to regex-only masking and the
        # run continues with `PII_ENGINE_DEGRADED` (v0.6 §12).
        "presidio": await _check_http(f"{analyzer}/health" if analyzer else None, required=False),
    }
    if langfuse := os.environ.get("LANGFUSE_HOST"):
        # Also optional: Postgres is the primary trace store, Langfuse is the second opinion.
        checks["langfuse"] = await _check_http(f"{langfuse}/api/public/health", required=False)

    ready = all(check.ok for check in checks.values() if check.required)
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadyResponse(ready=ready, checks=checks)
