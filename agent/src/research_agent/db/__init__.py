"""Persistence: the queue, the event store, the caches and the artifacts.

Alembic owns the schema (architecture v0.6 §17); the Go dispatcher only reads and writes rows,
it never migrates. `models` is therefore the single description of the database for both sides.
"""

from research_agent.db.models import (
    Base,
    EmbeddingCache,
    LlmCall,
    Preset,
    Run,
    RunArtifact,
    RunEvent,
    RunSecret,
    SearchCache,
    SearchCall,
)
from research_agent.db.session import (
    database_url,
    session_factory,
    sync_database_url,
)

__all__ = [
    "Base",
    "EmbeddingCache",
    "LlmCall",
    "Preset",
    "Run",
    "RunArtifact",
    "RunEvent",
    "RunSecret",
    "SearchCache",
    "SearchCall",
    "database_url",
    "session_factory",
    "sync_database_url",
]
