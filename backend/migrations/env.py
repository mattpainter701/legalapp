import asyncio
import os
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# Import Base and all models so Alembic can detect them
from app.database import Base
import app.models  # noqa: F401 — registers all models on Base.metadata

# Alembic Config object
config = context.config

# Migrations always prefer the owner/DDL credential. Runtime SQLAlchemy still
# receives DATABASE_URL=APP_DATABASE_URL (clarity_app) and remains NOBYPASSRLS.
database_url = os.environ.get("MIGRATOR_DATABASE_URL") or os.environ.get("DATABASE_URL")
if database_url:
    config.set_main_option("sqlalchemy.url", database_url)

# Set up Python logging from alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in offline mode (no DB connection needed)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        # Several historical FORCE-RLS policies use current_setting() without
        # ``missing_ok``. PostgreSQL can evaluate those policies while later
        # migrations alter constrained tables, even though the operation is
        # DDL. Give the NOBYPASSRLS migrator a non-customer sentinel context so
        # clean-host upgrades do not fail or accidentally expose tenant rows.
        connection.exec_driver_sql(
            "SELECT set_config('app.tenant_id', "
            "'00000000-0000-0000-0000-000000000000', true)"
        )
        connection.exec_driver_sql(
            "SELECT set_config('app.current_tenant_id', "
            "'00000000-0000-0000-0000-000000000000', true)"
        )
        connection.exec_driver_sql("SELECT set_config('app.rls_bypass', 'off', true)")
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in async mode."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in online mode."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
