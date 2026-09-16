"""The Settings screen's Test buttons. Keys pass through; nothing is stored."""

from __future__ import annotations

from fastapi import APIRouter

from research_agent.api.schemas import KeyValidationRequest, KeyValidationResponse
from research_agent.keys import Provider
from research_agent.providers.probe import probe

router = APIRouter(prefix="/api/keys", tags=["keys"])


@router.post("/validate", response_model=KeyValidationResponse)
async def validate_key(request: KeyValidationRequest) -> KeyValidationResponse:
    result = await probe(Provider(request.provider), request.key)
    return KeyValidationResponse(provider=result.provider.value, ok=result.ok, detail=result.detail)


@router.get("/status")
async def key_status() -> dict[str, dict[str, bool]]:
    """Which providers have a default key in `.env` - booleans only, for the status badges."""
    from research_agent.keys import ProviderKeys

    available = ProviderKeys.resolve().available()
    return {"env": {provider.value: provider in available for provider in Provider}}
