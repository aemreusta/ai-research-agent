"""The agent core: the graph, its nodes, and the pieces they share.

Deliberately free of infrastructure. The same code runs inside the `agent` service (driven by
the dispatcher) and inside the `research` CLI (driven by a person), which is what lets examples
and scenario tests exercise the real thing (architecture v0.6 §1).
"""
