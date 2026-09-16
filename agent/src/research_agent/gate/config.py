"""`config/gate.yaml` as a typed object."""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from research_agent.paths import config_dir


class Tolerance(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    numeric_relative: float = 0.02
    date_granularity_days: int = 31
    percentage_absolute: float = 0.5


class RuleSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow")

    severity: str
    description: str
    remediation: str


class GateConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int
    severity_order: list[str]
    rules: dict[str, RuleSpec]
    report_note_on_drop: str
    tolerance: Tolerance = Field(default_factory=Tolerance)
    required_sections: list[str] = Field(default_factory=list)

    @classmethod
    def load(cls, directory: Path | None = None) -> GateConfig:
        return _load(directory or config_dir())

    def rule(self, rule_id: str) -> RuleSpec:
        return self.rules[rule_id]


@cache
def _load(directory: Path) -> GateConfig:
    with (directory / "gate.yaml").open(encoding="utf-8") as handle:
        raw: dict[str, Any] = yaml.safe_load(handle)
    g4 = raw["rules"]["G4"]
    return GateConfig(
        version=raw["version"],
        severity_order=raw["severity_order"],
        rules=raw["rules"],
        report_note_on_drop=raw["report_note_on_drop"],
        tolerance=Tolerance(**g4.get("tolerance", {})),
        required_sections=list(raw["rules"]["G1"].get("required_sections", [])),
    )
