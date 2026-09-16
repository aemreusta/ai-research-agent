"""Loaders for the cross-language contracts in `contracts/`.

The YAML files are the single source of truth shared with the Go dispatcher; the enums below are
the statically typed Python view of them. `tests/unit/test_contracts.py` asserts that the two stay
in sync in both directions, so adding a code on one side only is a test failure, not a surprise in
production (architecture v0.6 §2, §13).
"""

from __future__ import annotations

from enum import StrEnum
from functools import cache
from typing import Any, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from research_agent.paths import contracts_dir


class RunStatus(StrEnum):
    """Run lifecycle states (v0.6 §2)."""

    QUEUED = "queued"
    DISPATCHED = "dispatched"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    SUCCEEDED_WITH_WARNINGS = "succeeded_with_warnings"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StateSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    owner: str
    transitions: tuple[RunStatus, ...]


class RunStateMachine(BaseModel):
    """The allowed run state transitions, as declared by `contracts/run_states.yaml`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int
    initial: RunStatus
    terminal: frozenset[RunStatus]
    max_attempts: int = Field(ge=1)
    states: dict[RunStatus, StateSpec]

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        missing = set(RunStatus) - set(self.states)
        if missing:
            raise ValueError(f"states missing from the contract: {sorted(missing)}")
        for status in self.terminal:
            if self.states[status].transitions:
                raise ValueError(f"terminal state {status} declares transitions")
        if self.initial in self.terminal:
            raise ValueError(f"initial state {self.initial} cannot be terminal")
        return self

    def can_transition(self, source: RunStatus, target: RunStatus) -> bool:
        return target in self.states[source].transitions

    def is_terminal(self, status: RunStatus) -> bool:
        return status in self.terminal


class ErrorCodeSpec(BaseModel):
    """Metadata for one error code.

    `expected` separates handled degradation from bugs: expected errors are logged at warn level
    with the error -> decision -> outcome chain, unexpected ones fail the run with a stack trace
    and a searchable event id (v0.6 §13.1).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    category: str
    expected: bool
    retryable: bool
    description: str


def _load_yaml(name: str) -> dict[str, Any]:
    with (contracts_dir() / name).open(encoding="utf-8") as handle:
        loaded: dict[str, Any] = yaml.safe_load(handle)
    return loaded


@cache
def run_state_machine() -> RunStateMachine:
    return RunStateMachine.model_validate(_load_yaml("run_states.yaml"))


@cache
def error_code_specs() -> dict[str, ErrorCodeSpec]:
    raw = _load_yaml("error_codes.yaml")
    return {name: ErrorCodeSpec.model_validate(spec) for name, spec in sorted(raw["codes"].items())}
