import secrets
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import schemas
from app.api.convert import user_out
from app.api.deps import current_user, require
from app.auth import COOKIE, SESSION_DAYS, hash_password, limiter, new_token, password_problem, session_expiry, token_hash, verify_password
from app.config import get_settings
from app.db import get_session
from app.models import AuthSession, Role, User

router = APIRouter(tags=["auth"])

RoleName = Literal["admin", "annotator", "reviewer", "operator", "viewer"]


class LoginIn(schemas.ApiModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=200)


class SetupIn(schemas.ApiModel):
    email: str = Field(min_length=3, max_length=320)
    display_name: str = Field(min_length=1, max_length=200)
    setup_token: str | None = Field(default=None, max_length=200)
    password: str = Field(max_length=200)


class PasswordIn(schemas.ApiModel):
    current_password: str
    new_password: str = Field(max_length=200)


class UserCreate(schemas.ApiModel):
    email: str = Field(min_length=3, max_length=320)
    display_name: str = Field(min_length=1, max_length=200)
    role: RoleName
    password: str = Field(max_length=200)


class UserPatch(schemas.ApiModel):
    role: RoleName | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, max_length=200)
    display_name: str | None = Field(default=None, min_length=1, max_length=200)


class UserAdminOut(schemas.UserOut):
    email: str
    is_active: bool
    can_sign_in: bool
    last_login_at: datetime | None


def _norm_email(e: str) -> str:
    e = e.strip().lower()
    if "@" not in e:
        raise HTTPException(422, "Enter an email address.")
    return e


def _check_password(p: str) -> None:
    if problem := password_problem(p):
        raise HTTPException(422, problem)


def _admin_out(u: User) -> UserAdminOut:
    base = user_out(u)
    return UserAdminOut(**base.model_dump(), email=u.email, is_active=u.is_active, can_sign_in=bool(u.password_hash), last_login_at=u.last_login_at)


async def _start_session(session: AsyncSession, user: User, request: Request, response: Response) -> None:
    token, digest = new_token()
    session.add(AuthSession(token_hash=digest, user_id=user.id, expires_at=session_expiry(), user_agent=(request.headers.get("user-agent") or "")[:300]))
    user.last_login_at = datetime.now(timezone.utc)
    await session.commit()
    response.set_cookie(
        COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax",
        secure=get_settings().cookie_secure, path="/",
    )


async def _setup_needed(session: AsyncSession) -> bool:
    n = await session.scalar(
        select(func.count()).select_from(User).where(User.role == Role.admin, User.is_active, User.password_hash.is_not(None))
    )
    return not n


@router.get("/auth/status")
async def status(session: AsyncSession = Depends(get_session)):
    """Public: whether the first admin account still has to be created, and whether that needs the setup code."""
    return {"setupNeeded": await _setup_needed(session), "setupTokenRequired": bool(get_settings().setup_token)}


@router.post("/auth/setup", response_model=schemas.UserOut)
async def first_admin(body: SetupIn, request: Request, response: Response, session: AsyncSession = Depends(get_session)):
    """Create the first admin. Only works while no admin can sign in."""
    if not await _setup_needed(session):
        raise HTTPException(409, "Setup is already done. Sign in instead.")
    expected = get_settings().setup_token
    if expected and not secrets.compare_digest((body.setup_token or "").encode(), expected.encode()):
        raise HTTPException(403, "The setup code is wrong. It's the SETUP_TOKEN set on the server.")
    _check_password(body.password)
    email = _norm_email(body.email)
    user = (await session.execute(select(User).where(func.lower(User.email) == email))).scalar_one_or_none()
    if user is None:
        user = User(email=email, display_name=body.display_name.strip(), role=Role.admin)
        session.add(user)
    user.display_name, user.role, user.is_active = body.display_name.strip(), Role.admin, True
    user.password_hash = hash_password(body.password)
    await session.flush()
    await _start_session(session, user, request, response)
    return user_out(user)


@router.post("/auth/login", response_model=schemas.UserOut)
async def login(body: LoginIn, request: Request, response: Response, session: AsyncSession = Depends(get_session)):
    email = _norm_email(body.email)
    if wait := limiter.blocked(email):
        raise HTTPException(429, f"Too many attempts. Try again in {max(1, wait // 60)} minute(s).")
    user = (await session.execute(select(User).where(func.lower(User.email) == email))).scalar_one_or_none()
    if not verify_password(body.password, user.password_hash if user else None) or not user or not user.is_active:
        limiter.fail(email)
        raise HTTPException(401, "That email and password don't match.")
    limiter.reset(email)
    await _start_session(session, user, request, response)
    return user_out(user)


@router.post("/auth/logout", status_code=204)
async def logout(request: Request, response: Response, session: AsyncSession = Depends(get_session)):
    if token := request.cookies.get(COOKIE):
        await session.execute(delete(AuthSession).where(AuthSession.token_hash == token_hash(token)))
        await session.commit()
    response.delete_cookie(COOKIE, path="/")


@router.get("/auth/me", response_model=schemas.UserOut)
async def me(user: User = Depends(current_user)):
    return user_out(user)


@router.post("/auth/password", status_code=204)
async def change_password(body: PasswordIn, request: Request, session: AsyncSession = Depends(get_session), user: User = Depends(current_user)):
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(403, "The current password is wrong.")
    _check_password(body.new_password)
    user.password_hash = hash_password(body.new_password)
    # sign out other devices, keep this one
    keep = token_hash(request.cookies.get(COOKIE, ""))
    await session.execute(delete(AuthSession).where(AuthSession.user_id == user.id, AuthSession.token_hash != keep))
    await session.commit()


# ---------- team management (admins) ----------
@router.get("/admin/users", response_model=list[UserAdminOut])
async def list_users_admin(session: AsyncSession = Depends(get_session), _a: User = Depends(require())):
    return [_admin_out(u) for u in (await session.execute(select(User).order_by(User.created_at))).scalars()]


@router.post("/admin/users", response_model=UserAdminOut, status_code=201)
async def create_user(body: UserCreate, session: AsyncSession = Depends(get_session), _a: User = Depends(require())):
    email = _norm_email(body.email)
    if await session.scalar(select(func.count()).select_from(User).where(func.lower(User.email) == email)):
        raise HTTPException(409, "Someone with this email already exists.")
    _check_password(body.password)
    u = User(email=email, display_name=body.display_name.strip(), role=Role(body.role), password_hash=hash_password(body.password))
    session.add(u)
    await session.commit()
    return _admin_out(u)


@router.patch("/admin/users/{user_id}", response_model=UserAdminOut)
async def update_user(user_id: uuid.UUID, body: UserPatch, session: AsyncSession = Depends(get_session), admin: User = Depends(require())):
    u = await session.get(User, user_id)
    if not u:
        raise HTTPException(404, "User not found.")
    if u.id == admin.id and (body.role not in (None, "admin") or body.is_active is False):
        raise HTTPException(422, "You can't remove your own admin access. Ask another admin.")
    if body.role is not None:
        u.role = Role(body.role)
    if body.display_name is not None:
        u.display_name = body.display_name.strip()
    if body.password is not None:
        _check_password(body.password)
        u.password_hash = hash_password(body.password)
    if body.is_active is not None:
        u.is_active = body.is_active
    if body.is_active is False or body.password is not None:
        await session.execute(delete(AuthSession).where(AuthSession.user_id == u.id))  # sign them out everywhere
    await session.commit()
    return _admin_out(u)
