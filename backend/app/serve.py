"""
Production entry point: the API under /api and the built frontend everywhere else, on one
address, so the sign-in cookie is never cross-site.

    uvicorn app.serve:app          (the Docker image runs this)
    uvicorn app.main:app --reload  (development: API only; Vite serves the UI and proxies /api)
"""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.main import app as api
from app.main import security_headers

ROOT = Path(get_settings().static_dir or Path(__file__).resolve().parents[2] / "frontend" / "dist").resolve()
INDEX = ROOT / "index.html"


class HashedAssets(StaticFiles):
    """Vite puts a content hash in every asset name, so browsers may keep them for good."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


# Mounted apps don't get lifespan events, so run the API's startup (migrations, Ask views) here.
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=api.router.lifespan_context)
app.middleware("http")(security_headers)
app.mount("/api", api)
if (ROOT / "assets").is_dir():
    app.mount("/assets", HashedAssets(directory=ROOT / "assets"), name="assets")


@app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)  # HEAD: hosts' and uptime monitors' probes
async def frontend(path: str):
    """Files from the build (favicon, samples, og.png); every other path is a page of the single-page app."""
    if not INDEX.exists():
        raise HTTPException(503, f"Frontend not built: {INDEX} is missing. Run npm run build in frontend/.")
    target = (ROOT / path).resolve()
    if path and target.is_file() and ROOT in target.parents:
        return FileResponse(target)
    return FileResponse(INDEX, headers={"Cache-Control": "no-cache"})
