"""
Small, idempotent schema upgrades applied at startup.

Until Alembic migrations are added, this keeps an existing local database working
when new columns or enum values appear, instead of needing a reset. Every statement
is safe to run on every start.
"""

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

log = logging.getLogger("schema")

PATCHES = [
    # v0.2
    "ALTER TABLE label_classes ADD COLUMN IF NOT EXISTS hotkey VARCHAR(4)",
    "ALTER TABLE label_classes ADD COLUMN IF NOT EXISTS sort_order INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE items ADD COLUMN IF NOT EXISTS assignee_id UUID REFERENCES users(id) ON DELETE SET NULL",
    "ALTER TABLE items ADD COLUMN IF NOT EXISTS thumb_key VARCHAR(1024)",
    "ALTER TABLE reviews ADD COLUMN IF NOT EXISTS item_id UUID REFERENCES items(id) ON DELETE CASCADE",
    "ALTER TABLE reviews ALTER COLUMN task_id DROP NOT NULL",
    "ALTER TYPE annotation_source ADD VALUE IF NOT EXISTS 'import'",
    # v0.3: import cancel, tiling, login
    "ALTER TYPE import_status ADD VALUE IF NOT EXISTS 'cancelled'",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash VARCHAR(256)",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ",
    "ALTER TABLE items ADD COLUMN IF NOT EXISTS tile_size INTEGER",
    "ALTER TABLE items ADD COLUMN IF NOT EXISTS tile_format VARCHAR(8)",
]


async def apply(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        for sql in PATCHES:
            try:
                await conn.execute(text(sql))
            except Exception as exc:  # e.g. the table doesn't exist in this database yet
                log.debug("schema patch skipped: %s (%s)", sql, exc)
