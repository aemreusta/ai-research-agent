"""The data plane's HTTP surface: execute, cancel, healthz, capacity.

Thin on purpose. Scheduling, retries and deadlines belong to the Go dispatcher; this service's
only job is to run a graph and keep the run row honest while it does (architecture v0.6 §2).
"""
