"""The deterministic Output Gate (architecture v0.6 §11).

No LLM call anywhere in this package: the same report and ledger always produce the same
verdict. That is the point - the gate is the last word on what reaches a user, and the one
component a prompt injection or a hallucinating model cannot talk round.
"""

from research_agent.gate.config import GateConfig
from research_agent.gate.runner import GateOutcome, run_gate

__all__ = ["GateConfig", "GateOutcome", "run_gate"]
