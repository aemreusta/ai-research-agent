"""Alembic environment.

The connection details come from `DATABASE_URL` through the same normaliser the application
uses, so `alembic upgrade head` in the `migrate` container and `alembic revision --autogenerate`
on a laptop always talk about the same database in the same dialect.
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool

from research_agent.db.models import Base, owned_by_us
from research_agent.db.session import sync_database_url

config = context.config
config.set_main_option("sqlalchemy.url", sync_database_url())

target_metadata = Base.metadata


def include_name(name: str | None, type_: str, parent_names: object) -> bool:
    # LangGraph's checkpoint tables share this database but are not ours to manage.
    return owned_by_us(name, type_)


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_name=include_name,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            include_name=include_name,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
