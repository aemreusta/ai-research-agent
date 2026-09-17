"""Checkpoint I/O fenced by the dispatch lease, in the same PostgreSQL transaction.

The row lock matters: a separate ownership check permits a revoked worker to overwrite the
new worker's checkpoint between that check and the write. Holding FOR SHARE until commit makes
revocation wait for the in-flight write, and every subsequent write sees the new lease.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection, AsyncCursor
from psycopg.rows import DictRow, dict_row

from research_agent.errors import LeaseLostError


class LeasedPostgresSaver(AsyncPostgresSaver):
    def __init__(
        self, connection: AsyncConnection[DictRow], *, run_id: uuid.UUID, lease_id: uuid.UUID
    ) -> None:
        super().__init__(connection)
        self._connection = connection
        self._run_id = run_id
        self._lease_id = lease_id

    @asynccontextmanager
    async def _cursor(self, *, pipeline: bool = False) -> AsyncIterator[AsyncCursor[DictRow]]:
        # The pinned LangGraph saver routes reads, checkpoints and pending writes through this
        # method. Use a transaction rather than a pipeline so the ownership check completes first.
        async with (
            self.lock,
            self._connection.transaction(),
            self._connection.cursor(binary=True, row_factory=dict_row) as cursor,
        ):
            await cursor.execute(
                "SELECT id FROM runs WHERE id = %s AND lease_id = %s "
                "AND status = 'running' FOR SHARE",
                (self._run_id, self._lease_id),
            )
            if await cursor.fetchone() is None:
                raise LeaseLostError(self._run_id)
            yield cursor


@asynccontextmanager
async def lease_checkpointer(
    dsn: str, *, run_id: uuid.UUID, lease_id: uuid.UUID
) -> AsyncIterator[LeasedPostgresSaver]:
    async with await AsyncConnection.connect(
        dsn, autocommit=True, prepare_threshold=0, row_factory=dict_row
    ) as connection:
        # Schema setup includes concurrent index creation, which cannot run in a transaction.
        # Serialize setup across replicas; normal per-run writes use the lease row lock above.
        await connection.execute("SELECT pg_advisory_lock(78138291)")
        try:
            await AsyncPostgresSaver(connection).setup()
        finally:
            await connection.execute("SELECT pg_advisory_unlock(78138291)")
        yield LeasedPostgresSaver(connection, run_id=run_id, lease_id=lease_id)
