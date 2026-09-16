"""Process entrypoints for the three roles of the Python image: `api`, `agent` and `migrate`.

One image, three commands (architecture v0.6 §2). Each builds its dependencies explicitly here
instead of at import time, so tests can construct the same apps with their own sessionmaker.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import uvicorn

from research_agent.config.loader import load_settings
from research_agent.db import session as db
from research_agent.keys import SecretBox, ensure_secret_key_file
from research_agent.observability.logging import configure_logging, get_logger


def _configure(service: str) -> None:
    settings = load_settings(env=os.environ).settings
    configure_logging(settings.logging, service=service)


def _uvicorn(app: object, *, port: int) -> None:
    uvicorn.run(
        app,  # type: ignore[arg-type]
        host=os.environ.get("APP_HOST", "0.0.0.0"),  # noqa: S104 - inside a container
        port=int(os.environ.get("APP_PORT", str(port))),
        # structlog owns the log format; uvicorn's access log would be a second, unredacted one.
        log_config=None,
        access_log=False,
        proxy_headers=True,
        timeout_graceful_shutdown=25,
    )


def serve_api() -> None:
    from research_agent.api.app import create_api_app

    _configure("api")
    app = create_api_app(
        sessionmaker=db.session_factory(),
        secret_box=SecretBox(),
        dsn=db.libpq_dsn(),
        on_shutdown=[db.dispose],
    )
    _uvicorn(app, port=8000)


def serve_agent() -> None:
    from research_agent.agent.factory import build_runner
    from research_agent.agent_server.app import agent_id_from_environment, create_agent_app
    from research_agent.agent_server.executor import RunExecutor

    _configure("agent")
    executor = RunExecutor(
        db.session_factory(),
        agent_id=agent_id_from_environment(),
        slots=int(os.environ.get("AGENT_SLOTS", "2")),
        runner=build_runner(),
        heartbeat_interval_seconds=float(os.environ.get("AGENT_HEARTBEAT_SECONDS", "10")),
        secret_box=SecretBox(),
    )
    _uvicorn(create_agent_app(executor), port=8081)


def migrate() -> None:
    """Schema to `head`, the shared encryption key, and (later) prompt seeds - idempotently."""
    from alembic import command
    from alembic.config import Config

    _configure("migrate")
    log = get_logger("migrate")

    if key_file := os.environ.get("APP_SECRET_KEY_FILE"):
        if ensure_secret_key_file(Path(key_file)):
            log.info("generated encryption key", path=key_file)
        else:
            log.info("encryption key present", path=key_file)

    root = Path(os.environ.get("APP_ROOT", Path(__file__).resolve().parents[3]))
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    command.upgrade(config, "head")
    log.info("schema at head")

    asyncio.run(db.dispose())
