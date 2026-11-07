# PostgreSQL 16 with PostGIS (annotation geometry) and pgvector (RAG embeddings).
# Starts from the official pgvector image (Debian 12 "bookworm") and adds PostGIS from
# PostgreSQL's own package repository, which that image already has set up.
# (postgis/postgis:16-3.4 was Debian 11, whose packages PostgreSQL no longer serves.)
FROM pgvector/pgvector:pg16

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-16-postgis-3 postgresql-16-postgis-3-scripts \
    && rm -rf /var/lib/apt/lists/*
