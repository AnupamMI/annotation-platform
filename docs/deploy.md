# Deploying Annotation ExE

The app ships as **one Docker image** that serves the landing page (`/`), the app (`/app/`) and the API (`/api/`) from one address. It needs two things outside it: a **PostgreSQL database with PostGIS** and **S3-compatible file storage**.

This guide uses free tiers that fit together:

| Part | Service | Free tier (at the time of writing; check current terms) |
|---|---|---|
| App (the Docker image) | [Render](https://render.com) | 512 MB memory; sleeps after 15 minutes without visits, about a minute to wake |
| Database | [Neon](https://neon.tech) | 0.5 GB; PostGIS, pgvector and pgcrypto available |
| Files (images, tiles, exports) | [Cloudflare R2](https://developers.cloudflare.com/r2/) or [Backblaze B2](https://www.backblaze.com/cloud-storage) | 10 GB each; R2 asks for a payment card on file, B2 doesn't |

> **No real patient data on free hosting.** Free tiers come with no data processing agreement and no guarantees. Use them for demos with public or synthetic images (the seed project, OpenSlide's test slides). Real clinical data needs hosting your institution has approved.

**Why not Vercel for everything?** Vercel runs websites and short serverless functions. The API needs a long-running Python process, a PostGIS database and big file uploads, so it runs on Render. Vercel can still serve the web pages (see [Option: web pages on Vercel](#option-web-pages-on-vercel)).

---

## 1. Database (Neon)

1. Create a project at neon.tech (any region near you; the same region as Render is best).
2. In **Connection details**, turn **off** "Connection pooling" and copy the connection string. The pooled connection doesn't work with the app's database driver (asyncpg).
3. Change it into the form the app expects:
   - `postgresql://` becomes `postgresql+asyncpg://`
   - `?sslmode=require` becomes `?ssl=require` (remove `&channel_binding=require` if present)

   ```
   postgresql+asyncpg://USER:PASSWORD@ep-xxxx.REGION.aws.neon.tech/neondb?ssl=require
   ```

You don't create tables or extensions yourself. On its first start the app runs the migrations, which enable PostGIS, pgvector and pgcrypto and create everything. It also creates the read-only role Ask uses (Neon's default account is allowed to).

## 2. File storage (pick one)

**Cloudflare R2**
1. In the Cloudflare dashboard, open R2 and create a bucket (for example `annotation-exe`).
2. **Manage R2 API tokens**: create a token with *Object Read & Write* on that bucket. Keep the Access Key ID and Secret Access Key.
3. Values for the app:
   - `S3_ENDPOINT=https://<ACCOUNT_ID>.r2.cloudflarestorage.com`
   - `S3_REGION=auto`
   - `S3_BUCKET`, `S3_ACCESS_KEY` and `S3_SECRET_KEY` from the previous steps

**Backblaze B2**
1. Create a **private** bucket. Its page shows the S3 endpoint, e.g. `s3.us-west-004.backblazeb2.com`.
2. **Application Keys**: add a key limited to that bucket, with read and write access.
3. Values for the app:
   - `S3_ENDPOINT=https://s3.us-west-004.backblazeb2.com`
   - `S3_REGION=us-west-004` (the middle part of the endpoint)
   - `S3_BUCKET` is the bucket name; `S3_ACCESS_KEY` is the keyID; `S3_SECRET_KEY` is the applicationKey

Create the bucket before the first start. The app checks it exists and stops with a clear message if it can't reach it.

## 3. The app (Render)

1. Push this repository to GitHub (the branch you want to deploy).
2. In Render: **New → Blueprint**, pick the repository. Render reads [render.yaml](../render.yaml) and asks for the values marked secret: `DATABASE_URL` from step 1, and the `S3_*` values from step 2.
3. Create it. The first build takes a few minutes (it builds the frontend and installs the Python packages). When it's live, Render shows the address, e.g. `https://annotation-exe.onrender.com`.
4. Check `https://<your address>/api/health`. It should say `"status": "ok"`. `"database": "unreachable"` means `DATABASE_URL` is wrong. `"missing_extensions"` means the extensions couldn't be enabled (see Troubleshooting).

### First admin

1. In Render, open the service's **Environment** tab and copy `SETUP_TOKEN`. Render generated a random one.
2. Open `https://<your address>/app/`. Because no admin exists yet, it shows **Create the admin account** and asks for the setup code. Without the code, nobody who stumbles on the address can claim the account first.
3. Sign in, then add the rest of the team in **Setup → Team**.

For a demo project with the sample images, run the seed once against the hosted database from your own computer:

```bash
cd backend
DATABASE_URL="postgresql+asyncpg://...?ssl=require" STORAGE_BACKEND=s3 S3_ENDPOINT=... S3_REGION=... \
  S3_BUCKET=... S3_ACCESS_KEY=... S3_SECRET_KEY=... python -m app.seed
```

On Windows PowerShell, set each variable with `$env:NAME = "value"` first.

### Settings

All settings are environment variables; [.env.example](../.env.example) lists them with explanations. The ones that matter in production:

| Variable | Value |
|---|---|
| `DATABASE_URL` | from step 1 |
| `STORAGE_BACKEND` | `s3` |
| `S3_ENDPOINT`, `S3_REGION`, `S3_BUCKET`, `S3_ACCESS_KEY`, `S3_SECRET_KEY` | from step 2 |
| `COOKIE_SECURE` | `true` (the sign-in cookie only travels over HTTPS) |
| `SETUP_TOKEN` | any long random string; only used until the first admin exists |
| `MAX_SLIDE_MB` | `2048` on the free tier; slides are copied to the instance's disk to be tiled |
| `LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY` | optional; lets Ask answer any question (e.g. `anthropic`, `claude-sonnet-5-5`) |
| `CORS_ORIGINS` | only if the web pages are served from a different address than the API |

## Updating

Push to the deployed branch. Render rebuilds and restarts, and the app applies any new migrations when it starts. No manual database steps.

If you change the models, create the migration on your computer first (`alembic revision --autogenerate -m "..."`, then read the file). CI fails if a model change has no migration.

## Backups

- **Database:** Neon's free tier keeps a short history you can restore from in its console. For your own copy:
  ```bash
  pg_dump "postgresql://USER:PASSWORD@HOST/neondb?sslmode=require" -Fc -f annotation-exe.dump
  ```
  This uses the plain `postgresql://` form with `sslmode`, not the app's `+asyncpg` form. Restore with `pg_restore -d <url> annotation-exe.dump`.
- **Files:** turn on object versioning or lifecycle rules in R2/B2 if you want deleted files kept. Thumbnails and tiles can be regenerated; originals can't.

## Option: web pages on Vercel

If you'd rather serve the landing page and app from Vercel (and keep the API on Render):

1. In [frontend/vercel.json](../frontend/vercel.json), set the `/api/:path*` destination to your Render address (it says `annotation-exe.onrender.com` now).
2. In Vercel: **New Project**, import the repository, and set **Root Directory** to `frontend`. Vercel detects Vite.
3. Leave `CORS_ORIGINS` alone. Vercel forwards `/api/...` to Render, so to the browser everything comes from the Vercel address and the sign-in cookie keeps working.

**Check large uploads after deploying.** Vercel limits the size of requests it forwards, which may stop big image or slide uploads through the Vercel address. If they fail, upload through the Render address, or serve everything from Render (the default setup).

Vercel's free Hobby plan is for non-commercial use.

## Run the production image yourself

On any machine with Docker (a VPS, a university server):

```bash
docker compose --profile full up -d --build   # database, storage and the app; open http://localhost:8080
```

Or run just the image against hosted services:

```bash
docker build -t annotation-exe .
docker run -p 8000:8000 --env-file .env annotation-exe
```

Put a web server with HTTPS in front of it (Caddy does this automatically) and set `COOKIE_SECURE=true`.

## Troubleshooting

| What you see | Cause |
|---|---|
| The first page load takes ~1 minute | The free Render instance was asleep. Paid instances don't sleep. |
| `/api/health` says `database: unreachable` | `DATABASE_URL` is wrong. Check the `+asyncpg`, `?ssl=require` and non-pooled host. |
| `missing_extensions` in `/api/health` | The database account can't create extensions. Neon: run `CREATE EXTENSION postgis; CREATE EXTENSION vector; CREATE EXTENSION pgcrypto;` in its SQL editor. |
| The service fails to start with "Storage bucket ... is missing" | The bucket name, endpoint, region or keys are wrong, or the key isn't allowed on that bucket. |
| Ask questions fail with a permission error mentioning `ask_readonly` (the start-up log warns "Ask view setup step failed") | The database account can't create roles. Run `ALTER ROLE <user> CREATEROLE;` as the owner, then restart. |
| Signed out straight after signing in | `COOKIE_SECURE=true` on plain HTTP, or the UI and API on different addresses without the `/api` proxy. |
| Slide uploads fail on the free tier | The instance ran out of disk or memory. Lower `MAX_SLIDE_MB`, or use a bigger instance. |
