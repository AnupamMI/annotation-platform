"""
Smoke test for the core data-model rules, against a real Postgres (PostGIS + pgvector).

Run from backend/ with the database up:
    python -m tests.smoke_data_model

It creates a throwaway project, checks the rules, and deletes the project again.
"""

import asyncio
import uuid

from sqlalchemy import select, text

from app.db import SessionLocal, engine
from app.models import (
    Annotation,
    Base,
    HierarchyLevel,
    Item,
    LabelClass,
    Node,
    Project,
    ShapeType,
)

CURRENT_ANNOTATIONS = text(
    """
    SELECT DISTINCT ON (annotation_id) annotation_id, version, is_deleted
    FROM annotations
    WHERE item_id = :item_id
    ORDER BY annotation_id, version DESC
    """
)

NUCLEI_OUTSIDE_THEIR_CELL = text(
    """
    WITH cur AS (
        SELECT DISTINCT ON (annotation_id) *
        FROM annotations WHERE item_id = :item_id
        ORDER BY annotation_id, version DESC
    ),
    live AS (SELECT * FROM cur WHERE NOT is_deleted)
    SELECT n.annotation_id
    FROM live n
    JOIN label_classes lc ON lc.id = n.label_class_id
    WHERE lc.must_be_inside_id IS NOT NULL
      AND NOT EXISTS (
        SELECT 1 FROM live c
        WHERE c.label_class_id = lc.must_be_inside_id
          AND ST_Within(n.geometry, c.geometry)
      )
    """
)


def box(x0: int, y0: int, x1: int, y1: int) -> str:
    return f"SRID=0;POLYGON(({x0} {y0},{x1} {y0},{x1} {y1},{x0} {y1},{x0} {y0}))"


async def main() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with SessionLocal() as s:
        project = Project(name=f"smoke-{uuid.uuid4().hex[:8]}", domain_pack="cytology")
        s.add(project)
        await s.flush()

        names = ["patient", "slide", "scan", "tile"]
        levels = [
            HierarchyLevel(project_id=project.id, depth=i, name=n, is_split_level=(n == "patient"))
            for i, n in enumerate(names)
        ]
        s.add_all(levels)
        await s.flush()

        patient = Node(project_id=project.id, level_id=levels[0].id, external_id="P-0001")
        s.add(patient)
        await s.flush()
        slide = Node(project_id=project.id, level_id=levels[1].id, parent_id=patient.id,
                     external_id="S-0001")
        s.add(slide)
        await s.flush()

        item = Item(project_id=project.id, node_id=slide.id, storage_key="smoke/tile.png",
                    original_filename="tile.png", sha256=uuid.uuid4().hex * 2,
                    width=1024, height=1024, size_bytes=1)
        cell = LabelClass(project_id=project.id, name="cell", allowed_shapes=["bbox", "polygon"])
        s.add_all([item, cell])
        await s.flush()
        nucleus = LabelClass(project_id=project.id, name="nucleus", allowed_shapes=["polygon"],
                             must_be_inside_id=cell.id)
        s.add(nucleus)
        await s.flush()

        # 1. Append-only versioning: create, edit, delete -> three rows, current = deleted.
        a1 = Annotation(item_id=item.id, label_class_id=cell.id, shape_type=ShapeType.bbox,
                        geometry=box(100, 100, 300, 300))
        s.add(a1)
        await s.flush()
        await s.refresh(a1)
        s.add(Annotation(annotation_id=a1.annotation_id, version=2, item_id=item.id,
                         label_class_id=cell.id, shape_type=ShapeType.bbox,
                         geometry=box(100, 100, 320, 320)))
        s.add(Annotation(annotation_id=a1.annotation_id, version=3, item_id=item.id,
                         label_class_id=cell.id, shape_type=ShapeType.bbox,
                         geometry=box(100, 100, 320, 320), is_deleted=True))
        await s.flush()
        rows = (await s.execute(select(Annotation).where(
            Annotation.annotation_id == a1.annotation_id))).scalars().all()
        assert len(rows) == 3, "edits must add rows, not overwrite"
        cur = (await s.execute(CURRENT_ANNOTATIONS, {"item_id": item.id})).all()
        assert cur[0].version == 3 and cur[0].is_deleted, "current state = latest version"
        print("ok  append-only versioning (3 rows, current = v3 tombstone)")

        # 2. Duplicate (annotation_id, version) must be rejected.
        try:
            async with s.begin_nested():
                s.add(Annotation(annotation_id=a1.annotation_id, version=3, item_id=item.id,
                                 label_class_id=cell.id, shape_type=ShapeType.bbox))
                await s.flush()
            raise AssertionError("duplicate version was accepted")
        except Exception as exc:
            assert "uq_annotations_annotation_id_version" in str(exc), exc
        print("ok  duplicate version rejected by database")

        # 3. Spatial rule: nucleus outside its cell is found with one query.
        live_cell = Annotation(item_id=item.id, label_class_id=cell.id,
                               shape_type=ShapeType.polygon, geometry=box(500, 500, 700, 700))
        inside = Annotation(item_id=item.id, label_class_id=nucleus.id,
                            shape_type=ShapeType.polygon, geometry=box(580, 580, 620, 620))
        outside = Annotation(item_id=item.id, label_class_id=nucleus.id,
                             shape_type=ShapeType.polygon, geometry=box(900, 900, 950, 950))
        s.add_all([live_cell, inside, outside])
        await s.flush()
        await s.refresh(outside)
        bad = (await s.execute(NUCLEI_OUTSIDE_THEIR_CELL, {"item_id": item.id})).scalars().all()
        assert bad == [outside.annotation_id], bad
        print("ok  'nucleus outside cell' found by one spatial query")

        await s.rollback()  # leave no trace

    await engine.dispose()
    print("all smoke checks passed")


if __name__ == "__main__":
    asyncio.run(main())
