"""
Test setup. Tests run against a real PostgreSQL with PostGIS (not SQLite), because
the geometry and versioning rules depend on it.

    TEST_DATABASE_URL=postgresql+asyncpg://annot:annot_dev_password@localhost:5432/annot_test pytest

The test database is wiped at the start of the run. Images go to a temporary folder.
Clients sign in through the real login endpoint.
"""

import io
import os
import tempfile

import pytest

TEST_DB = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://annot:annot_dev_password@localhost:5432/annot_test"
)
os.environ["DATABASE_URL"] = TEST_DB
os.environ["STORAGE_BACKEND"] = "local"
os.environ["LOCAL_STORAGE_DIR"] = tempfile.mkdtemp(prefix="annot-test-")
os.environ["CREATE_TABLES_ON_STARTUP"] = "false"

from httpx import ASGITransport, AsyncClient  # noqa: E402
from PIL import Image  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.ai import views as ask_views  # noqa: E402
from app.auth import hash_password  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base, Role, User  # noqa: E402

PASSWORD = "test-password-1"
USERS = {
    "admin": ("admin@test", "Test Admin", Role.admin),
    "reviewer": ("rev@test", "Test Reviewer", Role.reviewer),
    "annotator": ("ann@test", "Test Annotator", Role.annotator),
    "viewer": ("view@test", "Test Viewer", Role.viewer),
}


@pytest.fixture(scope="session", autouse=True)
async def database():
    async with engine.begin() as conn:
        for ext in ("postgis", "pgcrypto"):
            await conn.execute(text(f"CREATE EXTENSION IF NOT EXISTS {ext}"))
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS restricted"))
        await conn.execute(text("DROP SCHEMA IF EXISTS ask CASCADE"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await ask_views.ensure(engine)
    async with SessionLocal() as s:
        pw = hash_password(PASSWORD)
        s.add_all([User(email=e, display_name=n, role=r, password_hash=pw) for e, n, r in USERS.values()])
        await s.commit()
    yield
    await engine.dispose()


async def signed_in(who: str) -> AsyncClient:
    c = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    r = await c.post("/auth/login", json={"email": USERS[who][0], "password": PASSWORD})
    assert r.status_code == 200, r.text
    return c


@pytest.fixture(scope="session")
async def client():
    c = await signed_in("admin")
    yield c
    await c.aclose()


@pytest.fixture(scope="session")
async def reviewer():
    c = await signed_in("reviewer")
    yield c
    await c.aclose()


@pytest.fixture(scope="session")
async def annotator():
    c = await signed_in("annotator")
    yield c
    await c.aclose()


@pytest.fixture
async def anonymous():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


def png_bytes(w=400, h=300, color=(200, 150, 180)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "PNG")
    return buf.getvalue()
