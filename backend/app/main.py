import asyncio
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.ai import views as ask_views
from app.api import ai, activity, annotations, auth, demo, health, items, projects, transfer
from app.api.deps import current_user
from app.config import get_settings
from app.db import engine
from app.migrate import upgrade_to_head


@asynccontextmanager
async def lifespan(app: FastAPI):
    if get_settings().migrate_on_startup:
        await asyncio.to_thread(upgrade_to_head)
        await ask_views.ensure(engine)
    yield
    await engine.dispose()


app = FastAPI(title="Annotation ExE API", version="0.5.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in get_settings().cors_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_credentials=True,
    allow_headers=["*"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("X-Frame-Options", "DENY")
    return response


# Public: health check and sign-in. Everything else needs a signed-in user; endpoints add role checks.
app.include_router(health.router)
app.include_router(auth.router)
for r in (projects.router, items.router, annotations.router, activity.router, transfer.router, ai.router, demo.router):
    app.include_router(r, dependencies=[Depends(current_user)])
