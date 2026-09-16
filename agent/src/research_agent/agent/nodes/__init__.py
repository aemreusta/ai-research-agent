"""Graph nodes. Each is `async (state, deps, events) -> None` and mutates the state in place.

Every LLM-backed node has a deterministic fallback, so a provider outage degrades the answer
instead of failing the run (architecture v0.6 §5, table "Hata / fallback").
"""
