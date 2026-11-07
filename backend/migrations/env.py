"""Alembic environment: async engine from app settings, models' metadata for autogenerate."""

import asyncio
from logging.config import fileConfig

from alembic import context
from geoalchemy2 import alembic_helpers
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.models import Base

config = context.config
if config.config_file_name and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# Objects the app doesn't own: PostGIS's own tables, and the Ask views (rebuilt at every start).
IGNORED_SCHEMAS = {"ask", "tiger", "tiger_data", "topology"}
IGNORED_TABLES = {"spatial_ref_sys", "layer", "topology"}


def include_object(obj, name, type_, reflected, compare_to):
    if type_ == "table" and (name in IGNORED_TABLES or obj.schema in IGNORED_SCHEMAS):
        return False
    return alembic_helpers.include_object(obj, name, type_, reflected, compare_to)


def configure(connection=None, **kw):
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        include_schemas=True,
        include_name=lambda name, type_, parent: not (type_ == "schema" and name in IGNORED_SCHEMAS),
        include_object=include_object,
        process_revision_directives=alembic_helpers.writer,
        render_item=alembic_helpers.render_item,
        compare_type=True,
        **kw,
    )


def run_offline() -> None:
    configure(url=get_settings().database_url, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def _run(connection) -> None:
    configure(connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_online() -> None:
    engine = create_async_engine(get_settings().database_url)
    async with engine.connect() as conn:
        await conn.run_sync(_run)
    await engine.dispose()


if context.is_offline_mode():
    run_offline()
else:
    asyncio.run(run_online())
