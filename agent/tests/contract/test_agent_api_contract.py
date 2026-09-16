"""`contracts/agent-api.openapi.yaml` is what the Go dispatcher is written against.

The Python server is the other half of the same contract, so the file and the implementation are
compared here rather than trusted to stay aligned. The comparison is intentionally structural -
paths, methods, status codes, required fields - and not a byte-for-byte schema diff: the file
carries prose the dispatcher authors need, and FastAPI carries detail they do not.
"""

from __future__ import annotations

from typing import Any

import yaml

from research_agent.agent_server.app import create_agent_app
from research_agent.agent_server.executor import RunExecutor
from research_agent.contracts import error_code_specs
from research_agent.paths import contracts_dir


def _contract() -> dict[str, Any]:
    with (contracts_dir() / "agent-api.openapi.yaml").open(encoding="utf-8") as handle:
        loaded: dict[str, Any] = yaml.safe_load(handle)
    return loaded


def _served() -> dict[str, Any]:
    executor = RunExecutor(
        None,  # type: ignore[arg-type]
        agent_id="contract",
        slots=1,
        runner=None,  # type: ignore[arg-type]
    )
    schema: dict[str, Any] = create_agent_app(executor).openapi()
    return schema


def test_the_same_paths_exist_on_both_sides() -> None:
    assert set(_contract()["paths"]) == set(_served()["paths"])


def test_the_same_methods_and_status_codes_exist_on_both_sides() -> None:
    contract, served = _contract()["paths"], _served()["paths"]
    for path, operations in contract.items():
        for method, operation in operations.items():
            assert method in served[path], f"{method.upper()} {path} is not served"
            declared = set(operation["responses"])
            implemented = set(served[path][method]["responses"])
            # FastAPI adds 422 for request validation; the contract does not need to mention it.
            assert declared <= implemented | {"422"}, (
                f"{method.upper()} {path} is missing {declared - implemented}"
            )


def test_execute_requires_the_attempt_number_on_both_sides() -> None:
    """Attempt drives resume-from-checkpoint, so it may never become optional by accident."""
    contract = _contract()["components"]["schemas"]["ExecuteRequest"]
    served = _served()["components"]["schemas"]["ExecuteRequest"]
    assert contract["required"] == served["required"] == ["attempt"]


def test_both_sides_forbid_unknown_request_fields() -> None:
    """A typo in the dispatcher's JSON should fail loudly, not be silently ignored."""
    for name in ("ExecuteRequest", "CancelRequest"):
        assert _contract()["components"]["schemas"][name]["additionalProperties"] is False
        assert _served()["components"]["schemas"][name]["additionalProperties"] is False


def test_the_error_body_is_the_shared_taxonomy() -> None:
    served = _served()["components"]["schemas"]["ErrorBody"]
    assert set(served["required"]) == {"error_code", "message", "expected"}


def test_the_codes_the_agent_returns_are_in_the_shared_contract() -> None:
    """The Go client switches on these, so an invented code would be unhandled there."""
    codes = error_code_specs()
    for code in ("DISPATCH_DEFERRED", "AGENT_UNREACHABLE", "AGENT_HEARTBEAT_LOST"):
        assert code in codes
