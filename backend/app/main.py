from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import schema_patches
from app.ai import views as ask_views
from app.api import ai, activity, annotations, auth, health, items, projects, transfer
from app.api.deps import current_user
from app.config import get_settings
from app.db import engine
from app.models import Base


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Dev convenience only. Alembic migrations replace this before any real data exists.
    if get_settings().create_tables_on_startup:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await schema_patches.apply(engine)
        await ask_views.ensure(engine)
    yield
    await engine.dispose()


app = FastAPI(title="Annotation Platform API", version="0.4.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_credentials=True,
    allow_headers=["*"],
)
# Public: health check and sign-in. Everything else needs a signed-in user; endpoints add role checks.
app.include_router(health.router)
app.include_router(auth.router)
for r in (projects.router, items.router, annotations.router, activity.router, transfer.router, ai.router):
    app.include_router(r, dependencies=[Depends(current_user)])
