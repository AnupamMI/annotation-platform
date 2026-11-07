# Architecture and decisions

This records what was decided and why, so later changes are deliberate.

## 1. Product principles

1. **The annotator decides every label.** No model pre-labels or guesses. Optional assist tools (e.g. click-to-segment) only run when the annotator triggers them, and the result is still the annotator's to accept.
2. **AI manages data, not labels.** Tracking, importing, querying, splitting, exporting, checking consistency, reporting.
3. **Agents propose, humans approve.** Any action that writes data or contacts people needs explicit approval. Every agent run is logged.
4. **General core, domain packs.** The core knows about projects, hierarchies, images, labels and annotations. Domain specifics (cytology scan QC, pseudonymization defaults, label presets) live in packs. Cytology is the first pack.
5. **Images only in v1.** Normal images and very large tiled images. Video, text, audio and 3D are out of scope for v1.

## 2. System overview

```
Web app (React)  ──►  FastAPI backend  ◄──  AI layer (agents, ask, RAG)
                         │                        │
                      Workers                LLM adapter (API / local)
                         │
        PostgreSQL (+PostGIS, pgvector) · Redis · MinIO
```

- **Backend:** one FastAPI app with internal modules (modular monolith). Splitting into services is possible later; not needed for one developer.
- **Workers:** a Redis-backed queue for slow jobs: tiling, thumbnails, import processing, export, lineage records.
- **Storage:** Postgres is the single source of truth for structured data. MinIO holds image bytes, tiles and exports; the database stores keys and checksums only.

## 3. Data model decisions

### 3.1 Configurable hierarchy
- `hierarchy_levels` defines, per project, an ordered list of levels (e.g. patient, slide, scan, tile) with a metadata schema for each.
- `nodes` is a tree: each node belongs to a level and optionally has a parent. Images (`items`) attach to a node.
- One level can be marked as the **split level**: dataset splits never put one split-level node in two splits (prevents patient/farm/store leakage).

### 3.2 Annotations are append-only
- Every create, edit or delete inserts a new row in `annotations` with the same `annotation_id` and `version + 1`. Deletes are tombstone versions (`is_deleted = true`).
- The current state is the latest version per `annotation_id` that is not deleted.
- Datasets reference exact annotation **rows**, so a dataset is reproducible forever.

### 3.3 Coordinates
- Geometry is stored in **full-image pixel coordinates** (level 0 for pyramidal images), never tile coordinates. Annotations can cross tiles, survive re-tiling and move to WSI support unchanged.
- PostGIS `GEOMETRY` with SRID 0 (plain Cartesian pixels). Boxes are stored as polygons, with `shape_type` remembering the intent.

### 3.4 Identity and privacy
- The main schema only stores pseudonymous `external_id`s.
- Real identities live in the `restricted` schema. Only admin code paths read it. The LLM's database role never has access.
- Any node metadata field can be marked sensitive in the level's schema.

### 3.5 Time tracking
- The client sends heartbeats with active milliseconds; time stops counting after ~60 s without input. Stored in `events.active_ms`.
- Annotators are told what is tracked. The data is used to find hard items, not to rank people.

## 4. Import ("settle uploaded data")

Pipeline, all as one `import_batches` row:
1. **Inspect** – detect format in code (YOLO, COCO, CVAT XML, Label Studio). Not the LLM.
2. **Match images** – file name → checksum (sha256) → perceptual hash. Unmatched go to an "unlinked" list.
3. **Map labels** – exact match, then proposed mappings (LLM-assisted); unknown labels are never dropped silently.
4. **Convert coordinates** – to full-image pixels; handle normalization, scaling, tile offsets, EXIF rotation, masks → polygons.
5. **Validate** – out-of-bounds, zero-area, invalid polygons, duplicates, ambiguous empties.
6. **Conflicts** – keep both / replace / skip. Never overwrite silently.
7. **Report → approve → commit** – annotations written with `source = import` and `import_batch_id`.
8. **Rollback** – one click undoes the whole batch.

Test: export → import → identical annotations (round-trip) for every supported format.

## 5. AI layer

- **LLM adapter:** one interface; providers are config (`LLM_PROVIDER`). API model first, local later.
- **Ask (question → SQL):** LLM sees curated views only, runs as `ask_readonly`, with statement timeout and row limit. The UI shows the SQL with the answer.
- **RAG:** guidelines/SOPs chunked into pgvector; answers cite sections.
- **Agents:** fixed tool list, run with the requesting user's permissions, plan → approve → execute, logged in `agent_runs`. Agents have no tool that writes annotations (enforced in code).

First agents: dataset builder, consistency checker. Later: import assistant, work allocation, report drafts, lineage records.

## 6. Out of scope for v1
3D lab overview, video, text/audio annotation, multi-organization tenancy, local LLM.
(Whole-slide images (.svs, .ndpi, .scn, .bif) were moved into v1: read with OpenSlide, tiles made on demand, label photo never shown.)

## 7. Deployment
- One image (`Dockerfile`) serves the landing page, the app and the API on one address (`app/serve.py`), so the sign-in cookie is never cross-site.
- Schema changes are Alembic migrations, applied when the API starts. The baseline (0001) is frozen SQL from the v0.4 models; databases made before migrations get the old startup patches instead.
- Free hosting: Render (container), Neon (Postgres with PostGIS and pgvector), Cloudflare R2 or Backblaze B2 (files). See [deploy.md](deploy.md).

## 8. Open items
- ~~Project name~~: Annotation ExE.
- Licence decision (currently private; revisit with supervisor).
- Which public datasets for the two demo projects (one cytology, one non-medical).
