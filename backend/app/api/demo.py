"""Admin only: the demo project, to show the platform to someone with shareable non-admin logins."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app import schemas, seed
from app.api.deps import require
from app.db import get_session
from app.models import User

router = APIRouter(tags=["demo"])


class DemoLogin(schemas.ApiModel):
    email: str
    name: str
    role: str


class DemoStatus(schemas.ApiModel):
    project_id: uuid.UUID | None
    logins: list[DemoLogin]


class DemoReset(DemoStatus):
    password: str


LOGINS = [DemoLogin(email=e, name=n, role=r.value) for e, n, r in seed.DEMO_TEAM]


@router.get("/admin/demo", response_model=DemoStatus)
async def demo_status(session: AsyncSession = Depends(get_session), _a: User = Depends(require())):
    project = await seed.find_demo(session)
    return DemoStatus(project_id=project.id if project else None, logins=LOGINS)


@router.post("/admin/demo/reset", response_model=DemoReset)
async def demo_reset(session: AsyncSession = Depends(get_session), admin: User = Depends(require())):
    """Deletes the demo project (if any) with everything people did in it, and builds it fresh.
    The demo accounts get a new password, returned only here, and are signed out."""
    try:
        project, password = await seed.reset_demo(session, admin)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(409, str(exc))
    return DemoReset(project_id=project.id, logins=LOGINS, password=password)
