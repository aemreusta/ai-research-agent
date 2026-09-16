"""Request and response models for the browser-facing API.

Two rules run through all of them: no response ever carries key material (only `key_sources`),
and every validation bound the UI renders is re-checked here, because the browser is not a
trusted validator (D24).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_QUESTION_LENGTH = 2000


class CreateRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=MAX_QUESTION_LENGTH)
    overrides: dict[str, Any] = Field(
        default_factory=dict,
        description="Dotted paths from /api/config/schema, e.g. {'budget.max_searches': 30}.",
    )
    keys: dict[str, str] = Field(
        default_factory=dict,
        description="Provider keys from the browser's sessionStorage; stored encrypted, "
        "deleted when the run ends. Never echoed back.",
    )
    preset: str | None = Field(default=None, max_length=64)

    @field_validator("question")
    @classmethod
    def _not_only_whitespace(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("question cannot be blank")
        return stripped


class CreateRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: uuid.UUID
    status: str
    config_hash: str
    key_sources: dict[str, str]
    pii_masked: dict[str, int] = Field(
        description="What intake masking replaced, by kind - counts only, never values."
    )


class RunSummary(BaseModel):
    """One row of `#/runs`."""

    model_config = ConfigDict(extra="forbid")

    run_id: uuid.UUID
    status: str
    question: str
    stop_reason: str | None
    gate_status: str | None
    error_code: str | None
    iteration: int
    searches_used: int
    tokens_in: int
    tokens_out: int
    cost_usd: float
    created_at: datetime
    finished_at: datetime | None
    duration_seconds: float | None


class RunDetail(RunSummary):
    model_config = ConfigDict(extra="forbid")

    config_hash: str
    config_snapshot: dict[str, Any]
    overrides: dict[str, Any]
    key_sources: dict[str, str]
    models_used: dict[str, Any]
    prompt_versions: dict[str, Any]
    skills_used: list[Any]
    attempts: int
    agent_id: str | None
    cancel_requested: bool
    langfuse_trace_url: str | None
    error_message: str | None
    artifacts: list[str]
    report_md: str | None
    gate_result: dict[str, Any] | None


class RunList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runs: list[RunSummary]
    total: int


class CancelResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: uuid.UUID
    status: str
    cancel_requested: bool


class ConfigField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    group: str
    description: str
    type: str
    default: Any
    minimum: float | None = None
    maximum: float | None = None
    nullable: bool = False


class ConfigSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fields: list[ConfigField]
    groups: list[str]
    config_hash: str


class PresetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=500)
    overrides: dict[str, Any]


class Preset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    name: str
    description: str | None
    overrides: dict[str, Any]


class PresetList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    presets: list[Preset]


class KeyValidationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["gemini", "openai", "tavily", "brave", "ollama"]
    key: str = Field(min_length=1)


class KeyValidationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    ok: bool
    detail: str
    """Redacted; the key itself is never repeated back."""


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"]
    service: str
    version: str


class DependencyCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    detail: str
    required: bool


class ReadyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ready: bool
    checks: dict[str, DependencyCheck]


class ErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error_code: str
    message: str
    expected: bool
