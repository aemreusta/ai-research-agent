"""The `api` service: REST + SSE + the static UI, in one FastAPI app."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from research_agent import __version__
from research_agent.api.deps import ApiState
from research_agent.api.routes import config, health, keys, presets, runs
from research_agent.errors import AgentException
from research_agent.keys import SecretBox
from research_agent.observability.redaction import redact
from research_agent.pii.masking import Masker, RegexMasker

STATIC_DIR = Path(__file__).parent / "static"


def create_api_app(
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
    secret_box: SecretBox | None,
    dsn: str | None = None,
    masker: Masker | None = None,
    on_shutdown: list[object] | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        for hook in on_shutdown or []:
            if callable(hook):
                result = hook()
                if hasattr(result, "__await__"):
                    await result

    app = FastAPI(
        title="AI Research Agent",
        version=__version__,
        description="From a research question to a citation-verified report.",
        lifespan=lifespan,
    )
    app.state.api = ApiState(
        sessionmaker=sessionmaker,
        secret_box=secret_box,
        masker=masker or RegexMasker(),
        dsn=dsn,
    )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Validation errors echo the submitted value; a submitted value may be a key.
        return JSONResponse(
            status_code=422,
            content={
                "error_code": "CONFIG_INVALID",
                "message": "request failed validation",
                "expected": True,
                # Only location, message and type: `input` may be a key and `ctx` holds live
                # exception objects.
                "detail": redact(
                    [
                        {"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]}
                        for e in exc.errors()
                    ]
                ),
            },
        )

    @app.exception_handler(AgentException)
    async def agent_error(request: Request, exc: AgentException) -> JSONResponse:
        error = exc.error
        return JSONResponse(
            status_code=500 if not error.expected else 400,
            content={
                "error_code": error.code.value,
                "message": error.outcome or error.decision,
                "expected": error.expected,
            },
        )

    for module in (health, runs, config, presets, keys):
        app.include_router(module.router)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", media_type="text/html")

    @app.get("/api/meta", tags=["meta"])
    async def meta() -> dict[str, object]:
        """What the UI needs to know about its environment: version and deep links."""
        return {
            "version": __version__,
            "langfuse_url": os.environ.get("LANGFUSE_PUBLIC_URL") or None,
        }

    return app
