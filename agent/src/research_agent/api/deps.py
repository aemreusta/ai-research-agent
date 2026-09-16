"""Shared state for the request handlers.

Everything the API needs is built once in `create_api_app` and hung off `app.state`, so tests
construct an app with a test sessionmaker and no import-time globals get in the way.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from research_agent.keys import SecretBox
from research_agent.pii.masking import Masker


@dataclass(slots=True)
class ApiState:
    sessionmaker: async_sessionmaker[AsyncSession]
    secret_box: SecretBox | None
    masker: Masker
    dsn: str | None


def state(request: Request) -> ApiState:
    api_state: ApiState = request.app.state.api
    return api_state


Api = Annotated[ApiState, Depends(state)]
"""Handler parameter type: `api: Api`."""
