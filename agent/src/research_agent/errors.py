"""Error taxonomy: every failure gets a code, a category and an audit trail.

The point is not a tidy exception hierarchy but traceability (architecture v0.6 §13): an error is
only useful if you can also see the decision it triggered and the outcome that followed, e.g.
`SEARCH_TIMEOUT (tavily, 2/3) -> fallback brave -> OK 7 results`.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from research_agent.contracts import ErrorCodeSpec, error_code_specs


class ErrorCategory(StrEnum):
    SEARCH = "search"
    LLM = "llm"
    FETCH = "fetch"
    BUDGET = "budget"
    PII = "pii"
    GATE = "gate"
    CONFIG = "config"
    INFRA = "infra"
    BUG = "bug"


class ErrorCode(StrEnum):
    """Typed view of `contracts/error_codes.yaml` (kept in sync by a contract test)."""

    # search
    SEARCH_TIMEOUT = "SEARCH_TIMEOUT"
    SEARCH_RATE_LIMIT = "SEARCH_RATE_LIMIT"
    SEARCH_PROVIDER_ERROR = "SEARCH_PROVIDER_ERROR"
    SEARCH_AUTH = "SEARCH_AUTH"
    SEARCH_EMPTY = "SEARCH_EMPTY"
    DUPLICATE_QUERY = "DUPLICATE_QUERY"
    NO_EVIDENCE = "NO_EVIDENCE"
    # llm
    LLM_TIMEOUT = "LLM_TIMEOUT"
    LLM_RATE_LIMIT = "LLM_RATE_LIMIT"
    LLM_PROVIDER_ERROR = "LLM_PROVIDER_ERROR"
    LLM_AUTH = "LLM_AUTH"
    LLM_INVALID_OUTPUT = "LLM_INVALID_OUTPUT"
    LLM_REPAIR_FAILED = "LLM_REPAIR_FAILED"
    LLM_FALLBACK_USED = "LLM_FALLBACK_USED"
    EMBEDDING_UNAVAILABLE = "EMBEDDING_UNAVAILABLE"
    EXTRACT_QUOTE_NOT_FOUND = "EXTRACT_QUOTE_NOT_FOUND"
    # fetch
    FETCH_FAILED = "FETCH_FAILED"
    # budget
    MAX_ITERATIONS = "MAX_ITERATIONS"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    # pii
    PII_ENGINE_DEGRADED = "PII_ENGINE_DEGRADED"
    # gate
    GATE_REMEDIATED = "GATE_REMEDIATED"
    GATE_FAILED = "GATE_FAILED"
    # config
    CONFIG_INVALID = "CONFIG_INVALID"
    PROMPT_REGISTRY_DEGRADED = "PROMPT_REGISTRY_DEGRADED"
    PROMPT_SCHEMA_MISMATCH = "PROMPT_SCHEMA_MISMATCH"
    # infra
    AGENT_UNREACHABLE = "AGENT_UNREACHABLE"
    AGENT_HEARTBEAT_LOST = "AGENT_HEARTBEAT_LOST"
    DEADLINE_EXCEEDED = "DEADLINE_EXCEEDED"
    DISPATCH_DEFERRED = "DISPATCH_DEFERRED"
    RUN_LEASE_LOST = "RUN_LEASE_LOST"
    # bug
    UNEXPECTED_EXCEPTION = "UNEXPECTED_EXCEPTION"


def spec(code: ErrorCode) -> ErrorCodeSpec:
    """Category, expected/unexpected and retryability for a code, from the shared contract."""
    return error_code_specs()[code.value]


class AgentError(BaseModel):
    """A recorded failure: what broke, what the system decided, and what came of it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: ErrorCode
    node: str
    decision: str = Field(description='What was done: "retry", "fallback->brave", "mark exhausted"')
    outcome: str | None = Field(default=None, description='Result: "OK 7 results", "FAILED"')
    run_id: str | None = None
    span_id: str | None = None
    iteration: int | None = None
    provider: str | None = None
    attempt: int | None = None
    cause: str | None = Field(
        default=None, description="Redacted message; stack trace when unexpected"
    )

    @property
    def category(self) -> ErrorCategory:
        return ErrorCategory(spec(self.code).category)

    @property
    def expected(self) -> bool:
        """False means a bug: the run fails and the cause carries a stack trace."""
        return spec(self.code).expected

    @property
    def retryable(self) -> bool:
        return spec(self.code).retryable

    def log_fields(self) -> dict[str, object]:
        """The shared JSON field names used by both structlog and Go's slog (v0.6 §14)."""
        return {
            "error_code": self.code.value,
            "error_category": self.category.value,
            "expected": self.expected,
            "node": self.node,
            "decision": self.decision,
            "outcome": self.outcome,
            "run_id": self.run_id,
            "span_id": self.span_id,
            "iteration": self.iteration,
            "provider": self.provider,
            "attempt": self.attempt,
        }


class AgentException(Exception):
    """Raised when a coded failure has to unwind the stack instead of degrading in place."""

    def __init__(self, error: AgentError) -> None:
        super().__init__(f"{error.code.value}: {error.decision}")
        self.error = error


class LeaseLostError(AgentException):
    """This attempt has been superseded or the run has already terminated."""

    def __init__(self, run_id: object) -> None:
        super().__init__(
            AgentError(
                code=ErrorCode.RUN_LEASE_LOST,
                node="ownership",
                run_id=str(run_id),
                decision="stop the stale attempt without publishing state",
            )
        )
