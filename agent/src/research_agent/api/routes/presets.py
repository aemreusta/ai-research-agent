"""Named override sets, saved from the UI. Validated like a run; never written back to YAML."""

from __future__ import annotations

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from research_agent.api.deps import Api
from research_agent.api.schemas import ErrorResponse, Preset, PresetList, PresetRequest
from research_agent.config.loader import ConfigError, load_settings
from research_agent.db.models import Preset as PresetRow

router = APIRouter(prefix="/api/presets", tags=["presets"])


def _to_model(row: PresetRow) -> Preset:
    return Preset(id=row.id, name=row.name, description=row.description, overrides=row.overrides)


@router.get("", response_model=PresetList)
async def list_presets(api: Api) -> PresetList:
    async with api.sessionmaker() as session:
        rows = (await session.execute(select(PresetRow).order_by(PresetRow.name))).scalars().all()
    return PresetList(presets=[_to_model(row) for row in rows])


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=Preset,
    responses={400: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
async def create_preset(request: PresetRequest, api: Api) -> Preset | JSONResponse:
    # A preset that could never start a run is rejected when it is saved, not when it is used.
    try:
        load_settings(overrides=request.overrides)
    except ConfigError as exc:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=ErrorResponse(
                error_code=exc.error.code.value,
                message=exc.error.cause or exc.error.decision,
                expected=True,
            ).model_dump(),
        )

    row = PresetRow(name=request.name, description=request.description, overrides=request.overrides)
    async with api.sessionmaker() as session:
        session.add(row)
        try:
            await session.commit()
        except IntegrityError:
            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content=ErrorResponse(
                    error_code="CONFLICT",
                    message=f"a preset named '{request.name}' already exists",
                    expected=True,
                ).model_dump(),
            )
    return _to_model(row)


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_preset(name: str, api: Api) -> None:
    async with api.sessionmaker() as session:
        row = (
            await session.execute(select(PresetRow).where(PresetRow.name == name))
        ).scalar_one_or_none()
        if row is not None:
            await session.delete(row)
            await session.commit()
