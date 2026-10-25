"""
Export: current annotations of chosen images -> YOLO / COCO / CVAT XML / Label Studio.
Also used by the dataset builder, which adds train/val/test splits.
"""

from __future__ import annotations

import io
import json
import uuid
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import PurePosixPath

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.formats import ExportImage, Shape, write_export
from app.geo import from_db
from app.models import Annotation, Item, LabelClass, Node
from app.storage import get_storage


async def current_rows(session: AsyncSession, item_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[Annotation]]:
    """Latest, not-deleted version of every annotation on these images."""
    if not item_ids:
        return {}
    latest = (
        select(Annotation.annotation_id, func.max(Annotation.version).label("v"))
        .where(Annotation.item_id.in_(item_ids))
        .group_by(Annotation.annotation_id)
        .subquery()
    )
    rows = (
        await session.execute(
            select(Annotation)
            .join(latest, (Annotation.annotation_id == latest.c.annotation_id) & (Annotation.version == latest.c.v))
            .where(Annotation.is_deleted.is_(False))
            .order_by(Annotation.created_at)
        )
    ).scalars().all()
    out: dict[uuid.UUID, list[Annotation]] = defaultdict(list)
    for r in rows:
        out[r.item_id].append(r)
    return out


def unique_names(items: list[Item], nodes: dict[uuid.UUID, Node]) -> dict[uuid.UUID, str]:
    """File names must be unique in an export; the same name on two slides gets the slide ID in front."""
    count: dict[str, int] = defaultdict(int)
    for it in items:
        count[PurePosixPath(it.original_filename).name.casefold()] += 1
    out, used = {}, set()
    for it in items:
        name = PurePosixPath(it.original_filename).name
        ext = PurePosixPath(it.storage_key).suffix  # stored images may have been converted (e.g. TIFF -> PNG)
        name = PurePosixPath(name).stem + ext
        if count[PurePosixPath(it.original_filename).name.casefold()] > 1:
            node = nodes.get(it.node_id) if it.node_id else None
            name = f"{node.external_id if node else 'unlinked'}__{name}"
        base, n = name, 2
        while name.casefold() in used:
            name = f"{PurePosixPath(base).stem}_{n}{PurePosixPath(base).suffix}"
            n += 1
        used.add(name.casefold())
        out[it.id] = name
    return out


async def build_export(
    session: AsyncSession,
    project_id: uuid.UUID,
    items: list[Item],
    fmt: str,
    include_images: bool,
    splits: dict[uuid.UUID, str] | None = None,
    extra_manifest: dict | None = None,
) -> tuple[bytes, dict]:
    labels = (
        await session.execute(select(LabelClass).where(LabelClass.project_id == project_id).order_by(LabelClass.sort_order, LabelClass.name))
    ).scalars().all()
    label_name = {l.id: l.name for l in labels}
    nodes = {n.id: n for n in (await session.execute(select(Node).where(Node.project_id == project_id))).scalars()}
    rows = await current_rows(session, [i.id for i in items])
    names = unique_names(items, nodes)
    storage = get_storage()

    export_images: list[ExportImage] = []
    manifest_items = []
    used_labels: set[str] = set()
    for it in items:
        shapes = []
        for r in rows.get(it.id, []):
            name = label_name[r.label_class_id]
            used_labels.add(name)
            shape = "polygon" if r.shape_type.value == "mask" else r.shape_type.value
            shapes.append(Shape(name, shape, from_db(r.geometry, r.shape_type)))
        data = await run_in_threadpool(storage.get, it.storage_key) if include_images else None
        export_images.append(ExportImage(names[it.id], it.width, it.height, shapes, data, (splits or {}).get(it.id)))
        manifest_items.append({
            "itemId": str(it.id), "file": names[it.id], "sha256": it.sha256, "split": (splits or {}).get(it.id),
            "annotationRows": [str(r.id) for r in rows.get(it.id, [])],
        })
    # Label order: project order (stable class indexes for YOLO), only labels that appear plus all active ones
    ordered = [l.name for l in labels if not l.is_archived or l.name in used_labels]

    data, notes = await run_in_threadpool(write_export, fmt, export_images, ordered)
    manifest = {
        "exportedAt": datetime.now(timezone.utc).isoformat(),
        "projectId": str(project_id),
        "format": fmt,
        "labels": ordered,
        "images": len(export_images),
        "annotations": sum(len(e.shapes) for e in export_images),
        "notes": notes,
        "items": manifest_items,
        **(extra_manifest or {}),
    }
    buf = io.BytesIO(data)
    with zipfile.ZipFile(buf, "a", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
    return buf.getvalue(), manifest
