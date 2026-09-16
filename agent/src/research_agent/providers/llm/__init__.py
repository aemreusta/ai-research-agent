"""The single LLM access layer (D29): every model call in the system goes through `LLMGateway`.

One layer means fallback, retries, repair, redaction, cost accounting and tracing are written
once. Provider adapters speak plain HTTP rather than vendor SDKs, which keeps that layer small
and keeps a second LLM stack (and its supply chain) out of the runtime.
"""
