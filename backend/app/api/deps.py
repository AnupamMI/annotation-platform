"""Shared request dependencies: who is asking, and may they do this."""

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import COOKIE, token_hash
from app.db import get_session
from app.models import AuthSession, Project, User


async def current_user(request: Request, session: AsyncSession = Depends(get_session)) -> User:
    """The signed-in user, from the session cookie. 401 if there is none."""
    token = request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(401, "Please sign in.")
    s = await session.get(AuthSession, token_hash(token))
    now = datetime.now(timezone.utc)
    if not s or s.expires_at <= now:
        raise HTTPException(401, "Your session has ended. Please sign in again.")
    user = await session.get(User, s.user_id)
    if not user or not user.is_active:
        raise HTTPException(401, "This account is disabled.")
    if not s.last_seen_at or now - s.last_seen_at > timedelta(minutes=5):
        s.last_seen_at = now
        await session.commit()
    return user


async def get_project(project_id: uuid.UUID, session: AsyncSession) -> Project:
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found.")
    return project


ROLE_NAMES = {"admin": "admins", "annotator": "annotators", "reviewer": "reviewers", "operator": "scanner operators", "viewer": "viewers"}


def require(*roles: str):
    """The current user must have one of these roles. Admins can do everything."""

    async def check(user: User = Depends(current_user)) -> User:
        if user.role.value != "admin" and user.role.value not in roles:
            allowed = ", ".join(ROLE_NAMES[r] for r in ("admin", *roles))
            raise HTTPException(403, f"Only {allowed} can do this.")
        return user

    return check
