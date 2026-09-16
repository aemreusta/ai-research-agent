"""Config resolution: YAML defaults < environment (infrastructure only) < per-run UI overrides.

Every run records the effective configuration and its sha256 so a result can be reproduced and two
runs can be compared (architecture v0.6 §3). Overrides are validated twice on purpose: the UI
renders the bounds from the schema, and the server re-checks them here - the browser is not a
trusted validator.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from research_agent.config.schema import Settings, is_locked, known_paths
from research_agent.errors import AgentError, AgentException, ErrorCode
from research_agent.paths import config_dir

SETTINGS_FILE = "settings.yaml"

# The only settings the environment may touch. Everything else about agent behaviour belongs in
# YAML or in a per-run override, so that `.env` stays limited to secrets and infrastructure.
ENV_OVERRIDES: Mapping[str, str] = {
    "APP_LOG_LEVEL": "logging.level",
    "APP_LOG_FORMAT": "logging.format",
}


class ConfigError(AgentException):
    """Configuration or override rejected before the run starts (`CONFIG_INVALID`)."""

    def __init__(self, decision: str, cause: str | None = None) -> None:
        super().__init__(
            AgentError(code=ErrorCode.CONFIG_INVALID, node="config", decision=decision, cause=cause)
        )


class EffectiveConfig(BaseModel):
    """Validated settings plus the provenance needed to reproduce and audit a run."""

    model_config = ConfigDict(frozen=True)

    settings: Settings
    snapshot: dict[str, Any]
    config_hash: str
    sources: dict[str, str]

    def source_of(self, path: str) -> str:
        """`yaml`, `env` or `override` - shown in the run view next to each parameter."""
        return self.sources.get(path, "default")


def _read_yaml(directory: Path) -> dict[str, Any]:
    path = directory / SETTINGS_FILE
    if not path.is_file():
        raise ConfigError(f"missing {path}")
    with path.open(encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ConfigError(f"{path} must contain a mapping")
    return loaded


def _flatten(data: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in data.items():
        path = f"{prefix}{key}"
        if isinstance(value, Mapping):
            flat.update(_flatten(value, f"{path}."))
        else:
            flat[path] = value
    return flat


def _assign(target: dict[str, Any], path: str, value: Any) -> None:
    head, _, tail = path.partition(".")
    if not tail:
        target[head] = value
        return
    branch = target.setdefault(head, {})
    if not isinstance(branch, dict):
        raise ConfigError(f"cannot set {path}: {head} is a scalar")
    _assign(branch, tail, value)


def _check_paths(paths: Mapping[str, Any], *, allow_locked: bool) -> None:
    valid = known_paths()
    for path in paths:
        if path not in valid:
            raise ConfigError(f"unknown setting: {path}")
        if not allow_locked and is_locked(path):
            raise ConfigError(f"{path} cannot be overridden per run (security-critical setting)")


def load_settings(
    *,
    overrides: Mapping[str, Any] | None = None,
    env: Mapping[str, str] | None = None,
    directory: Path | None = None,
) -> EffectiveConfig:
    """Resolve the three layers into one validated, hashed configuration.

    `overrides` accepts dotted paths (`{"budget.max_searches": 30}`) or nested mappings; both are
    normalised before being checked against the schema.
    """
    directory = directory or config_dir()
    env = env if env is not None else {}

    raw = _read_yaml(directory)
    flat_yaml = _flatten(raw)
    _check_paths(flat_yaml, allow_locked=True)
    sources = dict.fromkeys(flat_yaml, "yaml")

    merged: dict[str, Any] = {}
    for path, value in flat_yaml.items():
        _assign(merged, path, value)

    for env_var, path in ENV_OVERRIDES.items():
        if (value := env.get(env_var)) is not None:
            _assign(merged, path, value)
            sources[path] = "env"

    flat_overrides = _flatten(overrides) if overrides else {}
    _check_paths(flat_overrides, allow_locked=False)
    for path, value in flat_overrides.items():
        _assign(merged, path, value)
        sources[path] = "override"

    try:
        settings = Settings.model_validate(merged)
    except ValidationError as exc:
        raise ConfigError("settings failed validation", cause=str(exc)) from exc

    snapshot = settings.model_dump(mode="json")
    return EffectiveConfig(
        settings=settings,
        snapshot=snapshot,
        config_hash=config_hash(snapshot),
        sources=sources,
    )


def config_hash(snapshot: Mapping[str, Any]) -> str:
    """Stable sha256 of an effective configuration.

    Canonical JSON, so the hash depends only on the resolved values - not on YAML key order or the
    order in which overrides arrived.
    """
    canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
