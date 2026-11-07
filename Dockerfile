# Annotation ExE: one image that serves the landing page, the app and the API on one address.
#   docker build -t annotation-exe .
#   docker run -p 8000:8000 --env-file .env annotation-exe
# The database and file storage are separate (docker compose locally, hosted services in production).

FROM node:20-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /srv/backend
COPY backend/requirements.txt .
RUN pip install -r requirements.txt
COPY backend/ .
COPY --from=web /web/dist /srv/frontend/dist

RUN useradd --create-home --uid 10001 app && mkdir -p /srv/data && chown app /srv/data
USER app
ENV STATIC_DIR=/srv/frontend/dist \
    SLIDE_CACHE_DIR=/tmp/annot-slides \
    LOCAL_STORAGE_DIR=/srv/data/storage

EXPOSE 8000
# PORT is set by hosts such as Render; proxy headers so HTTPS is seen behind their load balancer.
CMD uvicorn app.serve:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips="*"
