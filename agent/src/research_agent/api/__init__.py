"""The user-facing service: REST, SSE and the static UI.

`api` is the only service the browser talks to and the only one that sees provider keys arrive
in cleartext; it encrypts them into `run_secrets` immediately (architecture v0.6 §15).
"""
