"""
Import: settle an uploaded annotation export into a project.

Steps (docs/architecture.md section 4):
  inspect (format detection in code) -> match images -> suggest label mapping ->
  convert and validate shapes -> find conflicts -> report -> (user approves) -> commit
Nothing is written to annotations until commit, and a commit can be undone.
"""

from __future__ import annotations

import io
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import PurePosixPath

from shapely.geometry import MultiPolygon, Point, Polygon, box
from shapely.validation import make_valid
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.formats import FORMAT_NAMES, Parsed, Shape, read_upload
from app.formats.common import image_size
from app.formats.labelstudio import strip_upload_prefix
from app.geo import from_shapely, to_db
from app.models import (
    Annotation,
    AnnotationSource,
    Event,
    ImportBatch,
    ImportStatus,
    Item,
    ItemStatus,
    LabelClass,
    ShapeType,
    User,
)

PHASH_MAX_DISTANCE = 6  # of 64 bits
DUPLICATE_IOU = 0.95

ISSUE_TEXT = {
    "trimmed": ("fixed", "{n} shape(s) went past the image edge and were trimmed to it."),
    "repaired": ("fixed", "{n} outline(s) crossed over themselves and were repaired."),
    "scaled": ("fixed", "{n} image(s) were annotated at a different size; their coordinates were scaled to match."),
    "duplicate": ("fixed", "{n} duplicate shape(s) were removed (same label, almost the same outline)."),
    "zero_area": ("dropped", "{n} shape(s) had no area and will not be imported."),
    "point_outside": ("dropped", "{n} point(s) lay outside the image and will not be imported."),
    "invalid": ("dropped", "{n} outline(s) could not be repaired and will not be imported."),
    "ambiguous_empty": ("warn", "{n} image(s) have no annotations file entry: they may be unannotated or annotated with no objects. Nothing is imported for them."),
}


class ImportProblem(ValueError):
    pass


# ---------------- matching ----------------
def dhash_bytes(data: bytes) -> str | None:
    from PIL import Image, ImageOps

    from app.services.images import dhash

    try:
        return dhash(ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB"))
    except Exception:
        return None


def informative(phash: str) -> bool:
    """Blank or near-flat images all hash alike; don't match those by appearance."""
    bits = bin(int(phash, 16)).count("1")
    return 8 <= bits <= 56


def _hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def _name_keys(name: str) -> set[str]:
    base = PurePosixPath(name).name.casefold()
    stem = PurePosixPath(base).stem
    keys = {base, stem}
    stripped = strip_upload_prefix(base)
    keys |= {stripped, PurePosixPath(stripped).stem}
    # "FOV 01.jpg", "fov_01.png" and "FOV-01.tif" are the same image name
    keys |= {"~" + re.sub(r"[^a-z0-9]", "", PurePosixPath(k).stem) for k in list(keys)}
    keys.discard("~")
    return keys


@dataclass
class Target:
    """Where one export image's annotations go: an existing item or a new unlinked one."""

    key: str  # item id, or "new:<n>"
    item: Item | None
    width: int
    height: int
    file_names: list[str] = field(default_factory=list)
    shapes: list[tuple[Shape, dict, list[str]]] = field(default_factory=list)  # (original, resolved geometry, fixes)
    data: bytes | None = None


@dataclass
class Analysis:
    parsed: Parsed
    targets: dict[str, Target]
    rows: list[dict]  # per export image: file, how, itemId
    unmatched: list[dict]
    issues: dict[str, int]
    label_shapes: dict[str, dict[str, int]]


async def analyze(session: AsyncSession, project_id: uuid.UUID, parsed: Parsed, hold_unlinked: bool) -> Analysis:
    items = (await session.execute(select(Item).where(Item.project_id == project_id))).scalars().all()
    by_name: dict[str, list[Item]] = {}
    for it in items:
        names = {it.original_filename, (it.meta or {}).get("display_name") or ""}
        for k in set().union(*(_name_keys(n) for n in names if n)):
            by_name.setdefault(k, []).append(it)
    by_sha = {it.sha256: it for it in items}

    targets: dict[str, Target] = {}
    rows, unmatched = [], []
    issues: dict[str, int] = {}
    bump = lambda k, n=1: issues.__setitem__(k, issues.get(k, 0) + n)  # noqa: E731

    for idx, entry in enumerate(parsed.images):
        if entry.ambiguous_empty and not entry.shapes:
            bump("ambiguous_empty")
        item, how = None, None
        candidates = {c.id: c for k in _name_keys(entry.file_name) for c in by_name.get(k, [])}
        if len(candidates) == 1:
            item, how = next(iter(candidates.values())), "file name"
        if item is None and entry.data is not None:
            import hashlib

            item = by_sha.get(hashlib.sha256(entry.data).hexdigest())
            how = "checksum" if item else None
            if item is None:
                ph = dhash_bytes(entry.data)
                if ph and informative(ph):
                    best = min((it for it in items if it.phash and informative(it.phash)), key=lambda it: _hamming(it.phash, ph), default=None)
                    if best and _hamming(best.phash, ph) <= PHASH_MAX_DISTANCE:
                        item, how = best, "perceptual hash"

        if item is not None:
            t = targets.setdefault(str(item.id), Target(str(item.id), item, item.width, item.height))
            rows.append({"file": entry.file_name, "itemId": str(item.id), "how": how, "shapes": len(entry.shapes)})
        else:
            reason = "several project images have this name" if len(candidates) > 1 else "not in this project"
            unmatched.append({"file": entry.file_name, "hasImage": entry.data is not None, "reason": reason, "shapes": len(entry.shapes)})
            rows.append({"file": entry.file_name, "itemId": None, "how": "no match", "shapes": len(entry.shapes), "reason": reason})
            if not (hold_unlinked and entry.data is not None):
                continue
            w, h = image_size(entry.data)
            t = targets.setdefault(f"new:{idx}", Target(f"new:{idx}", None, w, h, data=entry.data))

        t.file_names.append(entry.file_name)
        sx = sy = 1.0
        if entry.width and entry.height and (abs(entry.width - t.width) > 0.5 or abs(entry.height - t.height) > 0.5):
            sx, sy = t.width / entry.width, t.height / entry.height
            bump("scaled")
        for s in entry.shapes:
            g, flags = resolve(s, t.width, t.height, sx, sy)
            if g is None:
                for f in flags:
                    bump(f)
            else:
                t.shapes.append((s, g, flags))

    for t in targets.values():
        kept: list[tuple[Shape, dict, list[str]]] = []
        for s, g, flags in t.shapes:
            if any(o.label == s.label and o.shape == s.shape and _same(og, g) for o, og, _ in kept):
                bump("duplicate")
                continue
            kept.append((s, g, flags))
            for f in flags:  # fixes are counted only for shapes that will be imported
                bump(f)
        t.shapes = kept

    label_shapes: dict[str, dict[str, int]] = {}
    for t in targets.values():
        for s, _, _ in t.shapes:
            d = label_shapes.setdefault(s.label, {})
            d[s.shape] = d.get(s.shape, 0) + 1
    return Analysis(parsed, targets, rows, unmatched, issues, label_shapes)


# ---------------- shape conversion and validation ----------------
def resolve(s: Shape, W: int, H: int, sx: float, sy: float) -> tuple[dict | None, list[str]]:
    fx, fy = (W, H) if s.normalized else (sx, sy)
    g = s.geometry
    flags: list[str] = []
    frame = box(0, 0, W, H)
    if s.shape == "point":
        x, y = g["x"] * fx, g["y"] * fy
        if not (-0.5 <= x <= W + 0.5 and -0.5 <= y <= H + 0.5):
            return None, ["point_outside"]
        return {"kind": "point", "x": round(min(max(x, 0), W), 1), "y": round(min(max(y, 0), H), 1)}, flags
    if s.shape == "bbox":
        x0, y0 = g["x"] * fx, g["y"] * fy
        x1, y1 = x0 + g["w"] * fx, y0 + g["h"] * fy
        x0, x1 = sorted((x0, x1))
        y0, y1 = sorted((y0, y1))
        cx0, cy0, cx1, cy1 = max(x0, 0), max(y0, 0), min(x1, W), min(y1, H)
        if (cx0, cy0, cx1, cy1) != (x0, y0, x1, y1) and max(abs(cx0 - x0), abs(cy0 - y0), abs(cx1 - x1), abs(cy1 - y1)) > 0.5:
            flags.append("trimmed")
        if cx1 - cx0 < 0.5 or cy1 - cy0 < 0.5:
            return None, flags + ["zero_area"]
        return {"kind": "bbox", "x": round(cx0, 1), "y": round(cy0, 1), "w": round(cx1 - cx0, 1), "h": round(cy1 - cy0, 1)}, flags
    polys = []
    for poly in g["polygons"]:
        rings = [[(x * fx, y * fy) for x, y in ring] for ring in poly]
        if len(rings[0]) < 4:
            continue
        polys.append(Polygon(rings[0], rings[1:]))
    if not polys:
        return None, ["zero_area"]
    geom = MultiPolygon(polys)
    if not geom.is_valid:
        flags.append("repaired")
        geom = make_valid(geom)
    if not geom.within(frame.buffer(0.5)):
        flags.append("trimmed")
        geom = geom.intersection(frame)
    parts = [p for p in getattr(geom, "geoms", [geom]) if isinstance(p, Polygon) and p.area >= 1]
    if not parts:
        return None, flags + (["invalid"] if "repaired" in flags else ["zero_area"])
    return from_shapely(MultiPolygon(parts), ShapeType.polygon), flags


def _shp(g: dict):
    if g["kind"] == "point":
        return Point(g["x"], g["y"])
    if g["kind"] == "bbox":
        return box(g["x"], g["y"], g["x"] + g["w"], g["y"] + g["h"])
    return MultiPolygon([Polygon(p[0], p[1:]) for p in g["polygons"]])


def _same(a: dict, b: dict) -> bool:
    A, B = _shp(a), _shp(b)
    if isinstance(A, Point):
        return A.distance(B) < 1.0
    union = A.union(B).area
    return union > 0 and A.intersection(B).area / union >= DUPLICATE_IOU


# ---------------- label suggestions ----------------
def _norm(s: str) -> str:
    """Lowercase, letters and digits only, singular (English and the Latin plurals common in biology)."""
    n = re.sub(r"[^a-z0-9]", "", s.casefold())
    if n.endswith(("us", "ss", "is")):  # already singular: nucleus, mass, analysis
        return n
    for plural, singular in (("ies", "y"), ("ei", "eus"), ("i", "us"), ("ae", "a"), ("ia", "ium"), ("es", ""), ("s", "")):
        if n.endswith(plural) and len(n) > len(plural) + 2:
            return n[: -len(plural)] + singular
    return n


def suggest(source: str, labels: list[LabelClass]) -> tuple[str | None, str]:
    """A suggestion only; nothing is imported until a person confirms the mapping."""
    from difflib import SequenceMatcher

    for l in labels:
        if l.name.casefold() == source.casefold():
            return str(l.id), "Same name"
    ns = _norm(source)
    for l in labels:
        if _norm(l.name) == ns:
            return str(l.id), "Same name, different spelling"
    scored = []
    for l in labels:
        nl = _norm(l.name)
        contained = len(ns) >= 3 and len(nl) >= 3 and (ns in nl or nl in ns)
        ratio = SequenceMatcher(None, ns, nl).ratio()
        if contained or ratio >= 0.8:
            scored.append((max(ratio, 0.8 if contained else 0), l))
    if scored:
        best = max(scored, key=lambda x: x[0])[1]
        return str(best.id), "Similar name; please check"
    return None, "No matching label in this project"


# ---------------- report ----------------
async def build_report(session: AsyncSession, batch: ImportBatch, a: Analysis, file_name: str) -> dict:
    labels = (
        await session.execute(select(LabelClass).where(LabelClass.project_id == batch.project_id, LabelClass.is_archived.is_(False)))
    ).scalars().all()
    label_rows = []
    for src in a.parsed.labels + [l for l in a.label_shapes if l not in a.parsed.labels]:
        shapes = a.label_shapes.get(src, {})
        if not shapes:
            continue
        sid, why = suggest(src, labels)
        label_rows.append({"source": src, "count": sum(shapes.values()), "shapes": shapes, "suggestedId": sid, "why": why})

    conflicts = []
    for t in a.targets.values():
        if t.item is None or not t.shapes:
            continue
        n = await live_count(session, t.item.id)
        if n:
            conflicts.append({"itemId": str(t.item.id), "name": (t.item.meta or {}).get("display_name") or PurePosixPath(t.item.original_filename).stem, "existing": n, "incoming": len(t.shapes)})

    issues = [{"kind": k, "level": ISSUE_TEXT[k][0], "count": n, "text": ISSUE_TEXT[k][1].format(n=n)} for k, n in a.issues.items()]
    issues += [{"kind": "parser", "level": "warn", "count": 1, "text": w} for w in a.parsed.warnings]
    matched = [r for r in a.rows if r["itemId"]]
    return {
        "fileName": file_name,
        "format": a.parsed.format,
        "formatName": FORMAT_NAMES[a.parsed.format],
        "images": a.rows,
        "unmatched": a.unmatched,
        "labels": label_rows,
        "issues": issues,
        "conflicts": conflicts,
        "totals": {
            "images": len(a.parsed.images),
            "matched": len({r["itemId"] for r in matched}),
            "unmatched": len(a.unmatched),
            "unmatchedWithImage": sum(1 for u in a.unmatched if u["hasImage"]),
            "annotations": sum(len(t.shapes) for t in a.targets.values() if t.item is not None),
        },
    }


async def live_count(session: AsyncSession, item_id: uuid.UUID) -> int:
    latest = (
        select(Annotation.annotation_id, func.max(Annotation.version).label("v"))
        .where(Annotation.item_id == item_id)
        .group_by(Annotation.annotation_id)
        .subquery()
    )
    return await session.scalar(
        select(func.count())
        .select_from(Annotation)
        .join(latest, (Annotation.annotation_id == latest.c.annotation_id) & (Annotation.version == latest.c.v))
        .where(Annotation.is_deleted.is_(False))
    ) or 0


# ---------------- commit ----------------
async def commit(
    session: AsyncSession,
    batch: ImportBatch,
    data: bytes,
    mapping: dict[str, str],
    conflict_policy: str,
    unlinked: str,
    user: User,
) -> dict:
    parsed = read_upload(batch.report.get("fileName", "upload"), data)
    a = await analyze(session, batch.project_id, parsed, hold_unlinked=unlinked == "hold")
    labels = {
        str(l.id): l
        for l in (await session.execute(select(LabelClass).where(LabelClass.project_id == batch.project_id, LabelClass.is_archived.is_(False)))).scalars()
    }

    missing = [src for src in a.label_shapes if src not in mapping]
    if missing:
        raise ImportProblem(f"Choose what to do with: {', '.join(missing)}.")
    converted = 0
    for src, target in mapping.items():
        if target == "skip" or src not in a.label_shapes:
            continue
        label = labels.get(target)
        if label is None:
            raise ImportProblem(f'"{src}" is mapped to a label that does not exist.')
        for shape in a.label_shapes[src]:
            if shape in label.allowed_shapes:
                continue
            if shape == "bbox" and "polygon" in label.allowed_shapes:
                converted += a.label_shapes[src][shape]
                continue
            raise ImportProblem(f'{a.label_shapes[src][shape]} "{src}" {shape}(s) can\'t become {label.name}, which only allows {", ".join(label.allowed_shapes)}.')

    from app.services import images
    from app.storage import get_storage

    storage = get_storage()
    written, touched = 0, 0
    skipped_items = 0
    for t in a.targets.values():
        if not t.shapes:
            continue
        item = t.item
        if item is not None:
            existing = await live_count(session, item.id)
            if existing and conflict_policy == "skip":
                skipped_items += 1
                continue
            if existing and conflict_policy == "replace":
                await _delete_live(session, item.id, user)
        else:
            info = await run_in_threadpool(images.analyze, t.data)
            dup = (await session.execute(select(Item).where(Item.project_id == batch.project_id, Item.sha256 == info["sha256"]))).scalar_one_or_none()
            if dup:
                item = dup
            else:
                item_id = uuid.uuid4()
                fields = await run_in_threadpool(images.store, storage, batch.project_id, item_id, info)
                fields.pop("tile_count", None)
                item = Item(
                    id=item_id, project_id=batch.project_id, node_id=None, original_filename=PurePosixPath(t.file_names[0]).name,
                    meta={"unlinked_from_import": str(batch.id)}, **fields,
                )
                session.add(item)
                await session.flush()
        n_here = 0
        for s, g, _ in t.shapes:
            target = mapping.get(s.label)
            if not target or target == "skip":
                continue
            label = labels[target]
            shape = s.shape
            if shape == "bbox" and "bbox" not in label.allowed_shapes:
                shape, g = "polygon", {"kind": "region", "polygons": [[[[g["x"], g["y"]], [g["x"] + g["w"], g["y"]], [g["x"] + g["w"], g["y"] + g["h"]], [g["x"], g["y"] + g["h"]], [g["x"], g["y"]]]]]}
            session.add(Annotation(
                annotation_id=uuid.uuid4(), version=1, item_id=item.id, label_class_id=label.id, shape_type=ShapeType(shape),
                geometry=to_db(g), attributes={}, source=AnnotationSource.import_, import_batch_id=batch.id, created_by=user.id,
            ))
            n_here += 1
        if n_here:
            written += n_here
            touched += 1
            item.status = ItemStatus.in_review  # imported work has not been reviewed in this project yet

    batch.status = ImportStatus.committed
    batch.label_mapping = mapping
    batch.conflict_policy = conflict_policy
    batch.approved_by = user.id
    batch.committed_at = datetime.now(timezone.utc)
    batch.report = {**batch.report, "committed": {"annotations": written, "images": touched, "skippedImages": skipped_items, "boxesAsPolygons": converted}}
    session.add(Event(project_id=batch.project_id, user_id=user.id, action="import.committed", payload={"detail": batch.report.get("fileName")}))
    await session.commit()
    return batch.report["committed"]


async def _delete_live(session: AsyncSession, item_id: uuid.UUID, user: User) -> None:
    rows = (await session.execute(select(Annotation).where(Annotation.item_id == item_id).order_by(Annotation.annotation_id, Annotation.version))).scalars().all()
    latest: dict[uuid.UUID, Annotation] = {}
    for r in rows:
        latest[r.annotation_id] = r
    for r in latest.values():
        if r.is_deleted:
            continue
        session.add(Annotation(
            annotation_id=r.annotation_id, version=r.version + 1, item_id=r.item_id, label_class_id=r.label_class_id,
            shape_type=r.shape_type, geometry=r.geometry, attributes=r.attributes, is_deleted=True,
            source=AnnotationSource.human, created_by=user.id,
        ))
    await session.flush()
