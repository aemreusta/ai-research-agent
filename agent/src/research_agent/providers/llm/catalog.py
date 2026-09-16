"""`config/models.yaml` as a typed object: which model serves which tier, and what it costs."""

from __future__ import annotations

from enum import StrEnum
from functools import cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from research_agent.paths import config_dir


class Tier(StrEnum):
    REASONING = "reasoning"
    FAST = "fast"
    EMBEDDING = "embedding"


class Price(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    input: float = Field(ge=0)
    output: float = Field(ge=0)


class ModelCatalog(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int
    chain: tuple[str, ...]
    tiers: dict[Tier, dict[str, str]]
    params: dict[Tier, dict[str, dict[str, Any]]] = Field(default_factory=dict)
    embedding_dimensions: int = Field(default=768, ge=64)
    prices_usd_per_million_tokens: dict[str, Price]

    @model_validator(mode="after")
    def _every_model_has_a_price(self) -> ModelCatalog:
        """An unpriced model would make the cost dashboard silently wrong."""
        missing = sorted(
            model
            for mapping in self.tiers.values()
            for model in mapping.values()
            if model not in self.prices_usd_per_million_tokens
        )
        if missing:
            raise ValueError(f"models without a price in models.yaml: {missing}")
        return self

    def model_for(self, tier: Tier, provider: str) -> str | None:
        return self.tiers.get(tier, {}).get(provider)

    def params_for(self, tier: Tier, provider: str) -> dict[str, Any]:
        return dict(self.params.get(tier, {}).get(provider, {}))

    def cost(self, model: str, tokens_in: int, tokens_out: int) -> float:
        price = self.prices_usd_per_million_tokens.get(model)
        if price is None:
            return 0.0
        return (tokens_in * price.input + tokens_out * price.output) / 1_000_000


def load_catalog(directory: Path | None = None) -> ModelCatalog:
    path = (directory or config_dir()) / "models.yaml"
    with path.open(encoding="utf-8") as handle:
        raw: dict[str, Any] = yaml.safe_load(handle)
    return ModelCatalog.model_validate(raw)


@cache
def catalog() -> ModelCatalog:
    return load_catalog()
