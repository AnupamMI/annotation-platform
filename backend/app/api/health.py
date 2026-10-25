from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session

router = APIRouter(tags=["health"])

REQUIRED_EXTENSIONS = ("postgis", "vector", "pgcrypto")


@router.get("/health")
async def health(session: AsyncSession = Depends(get_session)) -> dict:
    """Checks the database is reachable and the required extensions are installed."""
    try:
        rows = await session.execute(text("SELECT extname FROM pg_extension"))
        installed = {r[0] for r in rows}
    except Exception as exc:  # report, don't crash, so the problem is visible
        return {"status": "error", "database": "unreachable", "detail": str(exc)}

    missing = [e for e in REQUIRED_EXTENSIONS if e not in installed]
    return {
        "status": "ok" if not missing else "degraded",
        "database": "ok",
        "missing_extensions": missing,
    }
