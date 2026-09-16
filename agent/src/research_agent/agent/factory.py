"""Which `GraphRunner` the agent service runs.

`AGENT_RUNNER=echo` keeps the infrastructure testable without providers: the compose smoke test
uses it to prove queue, heartbeat and SSE end to end. The research graph is the default once it
exists.
"""

from __future__ import annotations

import os

from research_agent.agent.runner import EchoGraphRunner, GraphRunner


def build_runner(name: str | None = None) -> GraphRunner:
    choice = (name or os.environ.get("AGENT_RUNNER") or "echo").lower()
    if choice == "echo":
        delay = float(os.environ.get("AGENT_ECHO_DELAY_SECONDS", "0.5"))
        return EchoGraphRunner(delay_seconds=delay)
    raise ValueError(f"unknown AGENT_RUNNER {choice!r}; expected 'echo' or 'graph'")
