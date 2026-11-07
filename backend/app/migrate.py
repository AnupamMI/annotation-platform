"""Brings the database up to date with Alembic. Used at API startup; also `python -m app.migrate`."""

from pathlib import Path

from alembic import command
from alembic.config import Config

INI = Path(__file__).resolve().parent.parent / "alembic.ini"


def upgrade_to_head() -> None:
    """Blocking: call it from a worker thread inside async code (migrations start their own event loop)."""
    cfg = Config(str(INI))
    cfg.attributes["configure_logger"] = False  # keep the server's logging as it is
    command.upgrade(cfg, "head")


if __name__ == "__main__":
    upgrade_to_head()
    print("Database is up to date.")
