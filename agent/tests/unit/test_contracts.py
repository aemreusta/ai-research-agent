"""The contracts are shared with the Go dispatcher, so drift has to fail loudly here."""

from __future__ import annotations

import yaml

from research_agent.contracts import RunStatus, error_code_specs, run_state_machine
from research_agent.errors import ErrorCategory, ErrorCode, spec
from research_agent.paths import config_dir, contracts_dir


def test_error_code_enum_matches_contract_in_both_directions() -> None:
    from_yaml = set(error_code_specs())
    from_enum = {code.value for code in ErrorCode}
    assert from_enum == from_yaml, (
        f"only in the enum: {sorted(from_enum - from_yaml)}; "
        f"only in the YAML: {sorted(from_yaml - from_enum)}"
    )


def test_every_error_code_has_a_known_category() -> None:
    for code in ErrorCode:
        assert spec(code).category in {category.value for category in ErrorCategory}


def test_only_bugs_are_unexpected() -> None:
    unexpected = {code for code in ErrorCode if not spec(code).expected}
    assert unexpected == {ErrorCode.UNEXPECTED_EXCEPTION}
    assert spec(ErrorCode.UNEXPECTED_EXCEPTION).category == ErrorCategory.BUG


def test_auth_failures_are_not_retryable() -> None:
    # Retrying a rejected key just burns the budget; the UI has to ask for a new key instead.
    assert not spec(ErrorCode.SEARCH_AUTH).retryable
    assert not spec(ErrorCode.LLM_AUTH).retryable


def test_run_state_machine_covers_the_enum() -> None:
    machine = run_state_machine()
    assert set(machine.states) == set(RunStatus)
    assert machine.initial is RunStatus.QUEUED


def test_terminal_states_are_final() -> None:
    machine = run_state_machine()
    for status in machine.terminal:
        assert machine.is_terminal(status)
        assert not machine.states[status].transitions


def test_heartbeat_loss_can_requeue_a_running_run() -> None:
    # AGENT_HEARTBEAT_LOST puts the run back on the queue so another replica resumes it.
    machine = run_state_machine()
    assert machine.can_transition(RunStatus.RUNNING, RunStatus.QUEUED)
    assert machine.can_transition(RunStatus.DISPATCHED, RunStatus.QUEUED)


def test_a_finished_run_cannot_restart() -> None:
    machine = run_state_machine()
    assert not machine.can_transition(RunStatus.SUCCEEDED, RunStatus.RUNNING)
    assert not machine.can_transition(RunStatus.FAILED, RunStatus.QUEUED)


def test_dispatcher_retry_budget_matches_the_contract() -> None:
    # Two files, one number: the Go side reads dispatcher.yaml, the state machine lives in
    # run_states.yaml, and they have to agree on how often a run may be attempted.
    dispatcher = yaml.safe_load((config_dir() / "dispatcher.yaml").read_text(encoding="utf-8"))
    contract = yaml.safe_load((contracts_dir() / "run_states.yaml").read_text(encoding="utf-8"))
    assert dispatcher["retry"]["max_attempts"] == contract["max_attempts"]
