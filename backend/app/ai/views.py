"""
Curated, read-only views for Ask (question -> SQL).

They live in their own schema `ask`, show readable names instead of internal IDs
where it helps, and are scoped to one project: every view filters on
ask.current_project(), which reads a per-transaction setting the server sets
before running a query. The `ask_readonly` role can read these views and nothing
else (no tables, no restricted schema), and queries run read-only with a timeout.
"""

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

log = logging.getLogger("ask")

# Described to the language model; keep in sync with the SQL below.
SCHEMA_DOC = """
ask.images(image_id uuid, image_name text, status text  -- new | in_progress | annotated | in_review | done | rejected (done = approved),
           assignee text, location text  -- e.g. 'P-0101 / S-0101-A', top_level text  -- e.g. patient id,
           width int, height int, uploaded_at timestamptz, current_annotations int)
ask.hierarchy(node_id uuid, external_id text, level text, depth int  -- 0 = top level,
              parent_external_id text, images int  -- images directly under this node)
ask.annotations(annotation_id uuid, image_id uuid, image_name text, label text, shape text  -- bbox | polygon | point,
                area_px float, source text  -- human | tool_assisted | import, version int  -- 1 = never edited,
                last_edited_by text, last_edited_at timestamptz)   -- current version of each annotation, deleted ones excluded
ask.activity(user_name text, role text, action text  -- item.submitted | item.worked | review.approved | review.rejected | import.committed | export.created | dataset.created | items.uploaded,
             image_id uuid, image_name text, active_minutes numeric  -- active (non-idle) time this event covers, happened_at timestamptz)
ask.reviews(image_id uuid, image_name text, reviewer text, verdict text  -- approved | rejected, comment text, reviewed_at timestamptz)
ask.labels(label text, allowed_shapes text, must_be_inside text)
ask.imports(file_name text, format text, status text, uploaded_by text, created_at timestamptz)
ask.rule_problems(image_id uuid, image_name text, annotation_id uuid, label text, must_be_inside text)  -- shapes not inside their required parent
""".strip()

STATEMENTS = [
    "CREATE SCHEMA IF NOT EXISTS ask",
    """CREATE OR REPLACE FUNCTION ask.current_project() RETURNS uuid LANGUAGE sql STABLE AS
       $$ SELECT NULLIF(current_setting('app.project_id', true), '')::uuid $$""",
    """CREATE OR REPLACE VIEW ask.current_versions AS
       SELECT DISTINCT ON (a.annotation_id) a.*
       FROM annotations a JOIN items i ON i.id = a.item_id
       WHERE i.project_id = ask.current_project()
       ORDER BY a.annotation_id, a.version DESC""",
    """CREATE OR REPLACE VIEW ask.hierarchy AS
       SELECT n.id AS node_id, n.external_id, l.name AS level, l.depth, p.external_id AS parent_external_id,
              (SELECT count(*) FROM items i WHERE i.node_id = n.id)::int AS images
       FROM nodes n JOIN hierarchy_levels l ON l.id = n.level_id LEFT JOIN nodes p ON p.id = n.parent_id
       WHERE n.project_id = ask.current_project()""",
    """CREATE OR REPLACE VIEW ask.images AS
       WITH RECURSIVE up AS (
         SELECT n.id AS leaf_id, n.id, n.parent_id, n.external_id, 0 AS steps FROM nodes n WHERE n.project_id = ask.current_project()
         UNION ALL
         SELECT up.leaf_id, p.id, p.parent_id, p.external_id, up.steps + 1 FROM up JOIN nodes p ON p.id = up.parent_id
       ), paths AS (
         SELECT leaf_id, string_agg(external_id, ' / ' ORDER BY steps DESC) AS location,
                (array_agg(external_id ORDER BY steps DESC))[1] AS top_level
         FROM up GROUP BY leaf_id
       )
       SELECT i.id AS image_id,
              COALESCE(i.metadata->>'display_name', regexp_replace(i.original_filename, '\\.[^.]+$', '')) AS image_name,
              i.status::text AS status, u.display_name AS assignee, paths.location, paths.top_level,
              i.width, i.height, i.created_at AS uploaded_at,
              (SELECT count(*) FROM ask.current_versions c WHERE c.item_id = i.id AND NOT c.is_deleted)::int AS current_annotations
       FROM items i LEFT JOIN users u ON u.id = i.assignee_id LEFT JOIN paths ON paths.leaf_id = i.node_id
       WHERE i.project_id = ask.current_project()""",
    """CREATE OR REPLACE VIEW ask.annotations AS
       SELECT c.annotation_id, c.item_id AS image_id,
              COALESCE(i.metadata->>'display_name', regexp_replace(i.original_filename, '\\.[^.]+$', '')) AS image_name,
              l.name AS label, c.shape_type::text AS shape, ST_Area(c.geometry) AS area_px, c.source::text AS source,
              c.version, u.display_name AS last_edited_by, c.created_at AS last_edited_at
       FROM ask.current_versions c JOIN items i ON i.id = c.item_id JOIN label_classes l ON l.id = c.label_class_id
       LEFT JOIN users u ON u.id = c.created_by
       WHERE NOT c.is_deleted""",
    """CREATE OR REPLACE VIEW ask.activity AS
       SELECT u.display_name AS user_name, u.role::text AS role, e.action, e.item_id AS image_id,
              COALESCE(i.metadata->>'display_name', regexp_replace(i.original_filename, '\\.[^.]+$', '')) AS image_name,
              round(e.active_ms / 60000.0, 2) AS active_minutes, e.created_at AS happened_at
       FROM events e LEFT JOIN users u ON u.id = e.user_id LEFT JOIN items i ON i.id = e.item_id
       WHERE e.project_id = ask.current_project() AND e.action <> 'heartbeat'""",
    """CREATE OR REPLACE VIEW ask.reviews AS
       SELECT r.item_id AS image_id,
              COALESCE(i.metadata->>'display_name', regexp_replace(i.original_filename, '\\.[^.]+$', '')) AS image_name,
              u.display_name AS reviewer, r.verdict::text AS verdict, r.comment, r.created_at AS reviewed_at
       FROM reviews r JOIN items i ON i.id = r.item_id LEFT JOIN users u ON u.id = r.reviewer_id
       WHERE i.project_id = ask.current_project()""",
    """CREATE OR REPLACE VIEW ask.labels AS
       SELECT l.name AS label, array_to_string(l.allowed_shapes, ', ') AS allowed_shapes, p.name AS must_be_inside
       FROM label_classes l LEFT JOIN label_classes p ON p.id = l.must_be_inside_id
       WHERE l.project_id = ask.current_project() AND NOT l.is_archived""",
    """CREATE OR REPLACE VIEW ask.imports AS
       SELECT COALESCE(b.report->>'fileName', b.report->>'file_name') AS file_name, b.detected_format AS format,
              b.status::text AS status, u.display_name AS uploaded_by, b.created_at
       FROM import_batches b LEFT JOIN users u ON u.id = b.uploaded_by
       WHERE b.project_id = ask.current_project() AND b.status::text NOT IN ('cancelled', 'uploaded', 'inspecting')""",
    """CREATE OR REPLACE VIEW ask.rule_problems AS
       WITH live AS (SELECT * FROM ask.current_versions WHERE NOT is_deleted)
       SELECT a.item_id AS image_id,
              COALESCE(i.metadata->>'display_name', regexp_replace(i.original_filename, '\\.[^.]+$', '')) AS image_name,
              a.annotation_id, l.name AS label, p.name AS must_be_inside
       FROM live a JOIN label_classes l ON l.id = a.label_class_id JOIN label_classes p ON p.id = l.must_be_inside_id
       JOIN items i ON i.id = a.item_id
       WHERE NOT EXISTS (
         SELECT 1 FROM live c WHERE c.item_id = a.item_id AND c.label_class_id = l.must_be_inside_id
           AND ST_Within(a.geometry, c.geometry))""",
    """DO $$ BEGIN
         IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ask_readonly') THEN CREATE ROLE ask_readonly NOLOGIN; END IF;
       END $$""",
    "GRANT USAGE ON SCHEMA ask TO ask_readonly",
    "GRANT SELECT ON ALL TABLES IN SCHEMA ask TO ask_readonly",
    "GRANT EXECUTE ON FUNCTION ask.current_project() TO ask_readonly",
    # lets a non-superuser database account switch to the read-only role (no effect for superusers)
    "GRANT ask_readonly TO CURRENT_USER",
]


async def ensure(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        for sql in STATEMENTS:
            try:
                await conn.execute(text(sql))
            except Exception as exc:
                log.warning("Ask view setup step failed: %s", str(exc).splitlines()[0])
