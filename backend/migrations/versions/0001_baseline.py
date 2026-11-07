"""Baseline: the schema as it was when migrations were introduced (v0.4).

On an empty database, runs 0001_baseline.sql. On a database made before migrations
existed (tables created at startup), applies the old startup patches instead, which
bring it to the same state.

Revision ID: 0001
Revises:
"""

from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# What app/schema_patches.py used to apply at startup, kept for databases made before v0.4.
LEGACY_PATCHES = [
    "ALTER TABLE label_classes ADD COLUMN IF NOT EXISTS hotkey VARCHAR(4)",
    "ALTER TABLE label_classes ADD COLUMN IF NOT EXISTS sort_order INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE items ADD COLUMN IF NOT EXISTS assignee_id UUID REFERENCES users(id) ON DELETE SET NULL",
    "ALTER TABLE items ADD COLUMN IF NOT EXISTS thumb_key VARCHAR(1024)",
    "ALTER TABLE reviews ADD COLUMN IF NOT EXISTS item_id UUID REFERENCES items(id) ON DELETE CASCADE",
    "ALTER TABLE reviews ALTER COLUMN task_id DROP NOT NULL",
    "ALTER TYPE annotation_source ADD VALUE IF NOT EXISTS 'import'",
    "ALTER TYPE import_status ADD VALUE IF NOT EXISTS 'cancelled'",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash VARCHAR(256)",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ",
    "ALTER TABLE items ADD COLUMN IF NOT EXISTS tile_size INTEGER",
    "ALTER TABLE items ADD COLUMN IF NOT EXISTS tile_format VARCHAR(8)",
]


def statements(sql: str) -> list[str]:
    """Splits the SQL file at statement ends. asyncpg runs one statement per call."""
    lines = [ln for ln in sql.splitlines() if not ln.lstrip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";\n") if s.strip().rstrip(";")]


def upgrade() -> None:
    conn = op.get_bind()
    existing = conn.execute(text("SELECT to_regclass('public.projects') IS NOT NULL")).scalar()
    if existing:
        for sql in LEGACY_PATCHES:
            op.execute(sql)
        return
    for sql in statements((Path(__file__).with_suffix(".sql")).read_text(encoding="utf-8")):
        op.execute(sql.rstrip(";"))


def downgrade() -> None:
    raise NotImplementedError("The baseline can't be undone. Restore a backup instead.")
