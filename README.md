# Annotation ExE

[![CI](https://github.com/AnupamMI/annotation-platform/actions/workflows/ci.yml/badge.svg)](https://github.com/AnupamMI/annotation-platform/actions/workflows/ci.yml)

A general-purpose image annotation platform where **people make every labeling decision** and AI handles the data management around it: tracking, importing, querying and building datasets.

> Status: private, in development. Working end to end: public landing page, sign-in with roles, projects, uploads (large images are tiled, whole-slide images supported), versioned annotations, review, assignments, activity, import and export in four formats, guideline search, Ask and two agents. Ready to deploy: see [docs/deploy.md](docs/deploy.md).

## What makes it different

- **Configurable hierarchy per project.** Cytology: patient → slide → scan → tile. Agriculture: farm → field → flight → image. Retail: store → aisle → shelf photo. Progress, splits and analytics follow the hierarchy.
- **Annotator-led labeling.** No model guesses labels. Assist tools (if any) only act when the annotator triggers them.
- **Import that settles anything.** YOLO, COCO, CVAT XML and Label Studio uploads are matched to existing images, label-mapped, validated, conflict-checked and committed as one undoable batch.
- **Ask in plain language.** Questions like "how many slides are left this week?" become read-only SQL over curated views; the answer is shown with its query. Questions about the protocol are answered from your uploaded guidelines, with the section cited.
- **Safe agents.** Dataset builder and consistency checker propose a plan, you approve, they execute, everything is logged. Agents cannot create, edit or delete annotations.
- **Honest analytics.** Active time (with idle detection), not "time the tab was open".

## Stack

| Layer | Tech |
|---|---|
| Frontend | React + TypeScript, OpenSeadragon, PixiJS (WebGL annotation layer), React Three Fiber (3D overview, later) |
| Backend | FastAPI, SQLAlchemy 2 (async), GeoAlchemy2 |
| Database | PostgreSQL 16 + PostGIS (geometry) + pgvector (RAG) |
| Queue / cache | Redis |
| Object storage | MinIO (S3-compatible) |
| AI | Pluggable LLM adapter: API model first, local (Ollama/vLLM) later |

## Run it

Requirements: Docker Desktop, Python 3.11+, Node.js 18+.

```bash
# 1. Database, Redis and MinIO
cp .env.example .env              # Windows PowerShell: copy .env.example .env
docker compose up -d --build

# 2. API (first terminal)
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m app.seed                # once: loads the sample images as a demo project
uvicorn app.main:app --reload

# 3. UI (second terminal)
cd frontend
npm install
npm run dev
```

Open http://localhost:5173 for the landing page; the app itself is at http://localhost:5173/app/. The top bar shows **Saved** when the UI is talking to the API. If the API isn't running, the UI falls back to synthetic sample data and says **Sample data, not saved**.

When the API starts it brings the database up to date (Alembic migrations), so there is no separate step.

- API docs: http://localhost:8000/docs
- MinIO console: http://localhost:9001
- **Demo without signing in:** http://localhost:5173/demo/ runs the app on sample data in the browser; nothing is saved.
- **Signing in:** `python -m app.seed` prints the demo logins and a password once (set `DEMO_PASSWORD` to choose it, or run `python -m app.seed --reset-passwords`). `--reset` rebuilds the demo project fresh; on a deployed site, admins use **Projects → Demo project** instead. Without the seed, the first visit asks you to create the admin account. Admins add people and set roles in Setup → Team.

### Whole-slide images

Upload `.svs` (Aperio), `.ndpi` (Hamamatsu), `.scn` (Leica), `.bif` (Ventana) or pyramidal TIFF slides from **Data → Upload images**, like any other image. They are read with OpenSlide (installed by `requirements.txt`):

- the slide file is stored as-is and never loaded into memory whole (up to `MAX_SLIDE_MB`, 10 GB by default);
- overview tiles are made during upload, deeper tiles the first time someone zooms there, then kept;
- the scale bar, cursor position and areas use the slide's own microns per pixel;
- the label and macro photos inside the slide file are never extracted or shown, because the label can carry patient details. Use pseudonymous IDs for the patient and slide.

To try it, OpenSlide's public test slides work well, for example `CMU-1-JP2K-33005.svs` from https://openslide.cs.cmu.edu/download/openslide-testdata/Aperio/. Slide files are ignored by git (`*.svs` is in `.gitignore`).

### Ask and agents

Ask works without any AI service: common questions (progress, time per annotator, labels, reviews, rule warnings) use fixed read-only queries, guideline questions use full-text search over the documents in Setup → Guidelines, and agents plan from your wording. To let it answer any question, set `LLM_PROVIDER`, `LLM_MODEL` and `LLM_API_KEY` in `.env` (Claude, any OpenAI-compatible API, or a local model through Ollama).

How a question is kept safe, whatever the model writes:

1. The query must be one `SELECT`; write keywords, settings functions and quoting tricks are refused.
2. It runs in a read-only transaction, as the `ask_readonly` role, which can read only the `ask.*` views (no tables, no identity data).
3. The views only show the current project; there is a time limit and a row limit.
4. Every question, query and agent step is logged, with who approved what.

Agents (dataset builder, consistency checker) always show a plan first. Nothing runs until someone with the right role approves it, and no agent can create, edit or delete an annotation. Datasets are split by the project's split level (for example patient), so a patient never appears in two splits, and they freeze the exact annotation versions used.

If your database account is not a superuser, give it permission to manage roles once (`ALTER ROLE annot CREATEROLE;`) so the API can create the `ask_readonly` role at startup.

### The whole app in Docker

To run it the way it runs in production (one container serving the landing page, the app and the API):

```bash
docker compose --profile full up -d --build
```

Then open http://localhost:8080.

### Database changes (migrations)

The schema is managed with Alembic (`backend/migrations/`). The API runs `alembic upgrade head` when it starts, and databases made before migrations existed are brought in line automatically. After changing a model:

```bash
cd backend
alembic revision --autogenerate -m "what changed"   # then read the new file in migrations/versions/
alembic upgrade head
```

CI fails if the models and the migrations disagree (`alembic check`).

### Tests

The API tests run against a real PostgreSQL with PostGIS, using a separate `annot_test` database:

```bash
docker compose exec db createdb -U annot annot_test
cd backend
TEST_SLIDE=/path/to/CMU-1-JP2K-33005.svs pytest   # TEST_SLIDE is optional; without it the slide test is skipped
```

GitHub Actions runs the full suite on every push (with the slide test), checks the migrations, builds the frontend and builds and starts the deploy image.

## Deploy

[docs/deploy.md](docs/deploy.md) walks through a free setup: Render for the app (one Docker container), Neon for the database and Cloudflare R2 or Backblaze B2 for files, with Vercel as an option for the web pages.

## Project layout

```
annotation-platform/
├── backend/            FastAPI app
│   ├── app/models/     database tables
│   ├── app/api/        HTTP endpoints
│   ├── app/formats/    YOLO, COCO, CVAT XML, Label Studio readers and writers
│   ├── app/services/   import matching, export, image tiling, whole-slide images
│   ├── app/ai/         Ask (safe SQL, views), guideline search, agents, LLM adapter
│   ├── app/main.py     the API (development: uvicorn app.main:app)
│   ├── app/serve.py    production: the API under /api plus the built frontend
│   ├── app/seed.py     demo project from the sample images
│   ├── migrations/     Alembic database migrations
│   └── tests/          API tests (real Postgres), including format round trips and Ask safety
├── frontend/           React app
│   ├── src/landing/    public landing page (/) and 404
│   ├── src/pages/      one file per app screen (/app/...)
│   ├── src/components/ viewer, shell, upload, Ask panel, charts
│   ├── src/brand.ts    the product name, in one place
│   ├── src/api.ts      API client
│   └── src/store.ts    state: loads from the API, or sample data if it's not running
├── infra/postgres/     Postgres image with PostGIS + pgvector
├── docs/               architecture, decisions, deployment
├── .github/workflows/  CI
├── Dockerfile          the deploy image
├── render.yaml         Render blueprint (free hosting)
└── docker-compose.yml  local services (+ the whole app with --profile full)
```

## Roadmap

| Phase | Weeks | Milestone |
|---|---|---|
| 1 | 1–2 | Data model, upload, tiling, viewer |
| 2 | 3–5 | Annotation canvas, versioning, autosave |
| 3 | 6–7 | Import/export with round-trip tests, tasks, review |
| 4 | 8 | Analytics and dashboards |
| 5 | 9–10 | Ask panel and first agents |
| 6 | 11–12 | Polish, demo, deployment (landing page, deploy image, CI and migrations done) |

See [docs/architecture.md](docs/architecture.md) for the full design.
