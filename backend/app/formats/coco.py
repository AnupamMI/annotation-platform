"""
COCO JSON (instances). Boxes are [x, y, w, h] in pixels; polygons are flat
[x1, y1, x2, y2, ...] lists, one per part. RLE masks are not read (reported).
"""

from __future__ import annotations

import json

from app.formats.common import (
    Archive,
    ExportImage,
    FormatError,
    ImageEntry,
    Parsed,
    Shape,
    basename,
    closed,
    open_ring,
    ring_points,
    zip_bytes,
)


def _is_coco(doc) -> bool:
    return isinstance(doc, dict) and "images" in doc and "annotations" in doc and "categories" in doc


def find(archive: Archive) -> tuple[str, dict] | None:
    for path, data in archive.by_suffix(".json").items():
        try:
            doc = json.loads(data)
        except (ValueError, UnicodeDecodeError):
            continue
        if _is_coco(doc):
            return path, doc
    return None


def read(archive: Archive) -> Parsed:
    found = find(archive)
    if not found:
        raise FormatError("No COCO JSON (with images, annotations and categories) was found.")
    _, doc = found
    images = archive.images()
    cats = {c["id"]: str(c["name"]) for c in doc["categories"]}
    entries: dict[int, ImageEntry] = {}
    for im in doc["images"]:
        name = basename(str(im["file_name"]))
        entries[im["id"]] = ImageEntry(
            file_name=name, width=im.get("width"), height=im.get("height"), data=images.get(name.lower()), explicit_empty=True
        )
    rle, unknown = 0, 0
    for a in doc["annotations"]:
        entry = entries.get(a.get("image_id"))
        label = cats.get(a.get("category_id"))
        if entry is None or label is None:
            unknown += 1
            continue
        entry.explicit_empty = False
        seg = a.get("segmentation")
        if isinstance(seg, list) and seg and isinstance(seg[0], list):
            parts = [closed([[poly[i], poly[i + 1]] for i in range(0, len(poly) - 1, 2)]) for poly in seg if len(poly) >= 6]
            if parts:
                entry.shapes.append(Shape(label, "polygon", {"kind": "region", "polygons": [[p] for p in parts]}))
                continue
        if isinstance(seg, dict):
            rle += 1
        bbox = a.get("bbox")
        if bbox and len(bbox) == 4:
            x, y, w, h = (float(v) for v in bbox)
            entry.shapes.append(Shape(label, "bbox", {"kind": "bbox", "x": x, "y": y, "w": w, "h": h}))
    warnings = []
    if rle:
        warnings.append(f"{rle} mask(s) stored as RLE were imported as their bounding boxes. Export polygons instead to keep outlines.")
    if unknown:
        warnings.append(f"{unknown} annotation(s) pointed to an unknown image or category and were skipped.")
    return Parsed("coco", list(entries.values()), list(dict.fromkeys(cats.values())), warnings)


def write(images: list[ExportImage], labels: list[str]) -> tuple[bytes, list[str]]:
    cat_id = {name: i + 1 for i, name in enumerate(labels)}
    notes: list[str] = []
    files: dict[str, bytes] = {}
    skipped_points = holes = 0
    splits = sorted({im.split for im in images if im.split}) or [None]
    for sp in splits:
        doc = {
            "info": {"description": "Exported from Annotation Platform"},
            "images": [],
            "annotations": [],
            "categories": [{"id": cat_id[n], "name": n, "supercategory": ""} for n in labels],
        }
        aid = 1
        for img_id, im in enumerate((i for i in images if i.split == sp), start=1):
            doc["images"].append({"id": img_id, "file_name": im.file_name, "width": im.width, "height": im.height})
            for s in im.shapes:
                g = s.geometry
                if s.shape == "point":
                    skipped_points += 1
                    continue
                if s.shape == "bbox":
                    bbox = [g["x"], g["y"], g["w"], g["h"]]
                    seg: list = []
                    area = g["w"] * g["h"]
                else:
                    from app.geo import to_shapely

                    geom = to_shapely(g)
                    holes += sum(len(poly) - 1 for poly in g["polygons"])
                    seg = [[v for x, y in open_ring(r) for v in (x, y)] for r in ring_points(g)]
                    x0, y0, x1, y1 = geom.bounds
                    bbox = [x0, y0, x1 - x0, y1 - y0]
                    area = geom.area
                doc["annotations"].append({
                    "id": aid, "image_id": img_id, "category_id": cat_id[s.label], "bbox": [round(v, 2) for v in bbox],
                    "segmentation": [[round(v, 2) for v in r] for r in seg], "area": round(area, 2), "iscrowd": 0,
                })
                aid += 1
            if im.data is not None:
                files[f"images/{sp + '/' if sp else ''}{im.file_name}"] = im.data
        files[f"annotations/instances_{sp or 'default'}.json"] = json.dumps(doc).encode()
    if skipped_points:
        notes.append(f"{skipped_points} point annotation(s) were left out: COCO instances have no point shape.")
    if holes:
        notes.append(f"{holes} hole(s) inside outlines were dropped: COCO polygons can't store holes.")
    return zip_bytes(files), notes
