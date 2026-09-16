"""Config resolution: YAML defaults, the environment allowlist, and per-run overrides."""

from __future__ import annotations

from pathlib import Path

import pytest

from research_agent.config import load_settings
from research_agent.config.loader import ConfigError, config_hash
from research_agent.config.schema import Settings, is_locked, tunable_fields
from research_agent.errors import ErrorCode


def test_repo_yaml_validates_against_the_schema() -> None:
    effective = load_settings()
    assert isinstance(effective.settings, Settings)
    assert effective.settings.budget.max_searches == 45


def test_wall_clock_and_cost_gates_ship_disabled() -> None:
    # D11: both are real code paths, switched on per run from the UI, off by default.
    budget = load_settings().settings.budget
    assert budget.max_wall_clock_seconds is None
    assert budget.max_cost_usd is None


def test_override_applies_and_is_attributed() -> None:
    effective = load_settings(overrides={"budget.max_searches": 12})
    assert effective.settings.budget.max_searches == 12
    assert effective.source_of("budget.max_searches") == "override"
    assert effective.source_of("budget.max_iterations") == "yaml"


def test_nested_override_form_is_accepted() -> None:
    effective = load_settings(overrides={"budget": {"max_iterations": 2}})
    assert effective.settings.budget.max_iterations == 2


def test_override_outside_the_declared_bounds_is_rejected() -> None:
    with pytest.raises(ConfigError) as caught:
        load_settings(overrides={"budget.max_iterations": 99})
    assert caught.value.error.code is ErrorCode.CONFIG_INVALID


def test_unknown_setting_is_rejected() -> None:
    with pytest.raises(ConfigError, match="unknown setting"):
        load_settings(overrides={"budget.max_tokens": 10})


@pytest.mark.parametrize("path", ["pii.enabled", "gate.enabled"])
def test_security_critical_settings_cannot_be_overridden(path: str) -> None:
    # The browser renders the form, but the server decides what is tunable (v0.6 §3).
    assert is_locked(path)
    with pytest.raises(ConfigError, match="security-critical"):
        load_settings(overrides={path: False})


def test_locked_fields_are_hidden_from_the_ui_schema() -> None:
    paths = {row.path for row in tunable_fields()}
    assert "pii.enabled" not in paths
    assert "gate.enabled" not in paths
    assert "budget.max_searches" in paths
    assert {row.path for row in tunable_fields(include_locked=True)} > paths


def test_every_tunable_field_declares_a_group_and_description() -> None:
    for row in tunable_fields(include_locked=True):
        assert row.group != "Other", f"{row.path} has no UI group"
        assert row.description, f"{row.path} has no description"


def test_environment_may_only_touch_the_allowlist() -> None:
    effective = load_settings(env={"APP_LOG_LEVEL": "DEBUG", "APP_SECRET_KEY": "irrelevant"})
    assert effective.settings.logging.level == "DEBUG"
    assert effective.source_of("logging.level") == "env"


def test_override_beats_environment() -> None:
    effective = load_settings(
        env={"APP_LOG_LEVEL": "DEBUG"}, overrides={"logging.level": "WARNING"}
    )
    assert effective.settings.logging.level == "WARNING"


def test_config_hash_is_stable_and_order_independent() -> None:
    first = load_settings(overrides={"budget.max_searches": 20, "budget.max_iterations": 3})
    second = load_settings(overrides={"budget.max_iterations": 3, "budget.max_searches": 20})
    assert first.config_hash == second.config_hash
    assert first.config_hash != load_settings().config_hash


def test_config_hash_ignores_key_order_in_the_snapshot() -> None:
    snapshot = load_settings().snapshot
    reversed_snapshot = dict(reversed(list(snapshot.items())))
    assert config_hash(snapshot) == config_hash(reversed_snapshot)


def test_scoring_weights_must_sum_to_one() -> None:
    with pytest.raises(ConfigError):
        load_settings(overrides={"scoring.weights.authority": 0.9})


def test_missing_settings_file_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="missing"):
        load_settings(directory=tmp_path)
