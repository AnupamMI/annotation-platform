"""
Label Studio JSON export (list of tasks). Coordinates are percentages of the image.
Supports rectanglelabels, polygonlabels and keypointlabels; brush masks and
choices are reported, not read.
"""

from __future__ import annotations

import json
import re
import uuid

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

UPLOAD_PREFIX = re.compile(r"^[0-9a-f]{8}-", re.I)  # Label Studio renames uploads to "<hash>-name.jpg"


def strip_upload_prefix(name: str) -> str:
    return UPLOAD_PREFIX.sub("", name)


def find(archive: Archive):
    for path, data in archive.by_suffix(".json").items():
        try:
            doc = json.loads(data)
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(doc, list) and doc and isinstance(doc[0], dict) and "data" in doc[0]:
            return path, doc
    return None


def read(archive: Archive) -> Parsed:
    found = find(archive)
    if not found:
        raise FormatError("No Label Studio JSON export (a list of tasks) was found.")
    _, tasks = found
    images = archive.images()
    labels: list[str] = []
    entries: list[ImageEntry] = []
    skipped: dict[str, int] = {}
    rotated = 0
    for task in tasks:
        data = task.get("data") or {}
        url = next((v for v in data.values() if isinstance(v, str) and re.search(r"\.(jpe?g|png|bmp|tiff?|webp)(\?|$)", v, re.I)), None)
        if not url:
            skipped["task without an image"] = skipped.get("task without an image", 0) + 1
            continue
        name = basename(url)
        anns = [a for a in (task.get("annotations") or task.get("completions") or []) if not a.get("was_cancelled")]
        entry = ImageEntry(file_name=name, data=images.get(name.lower()) or images.get(strip_upload_prefix(name).lower()))
        entry.ambiguous_empty = not anns
        entry.explicit_empty = bool(anns)
        for r in (anns[0].get("result", []) if anns else []):
            t, v = r.get("type"), r.get("value") or {}
            W, H = r.get("original_width"), r.get("original_height")
            if W and H:
                entry.width, entry.height = int(W), int(H)
            names = v.get(t) if isinstance(v.get(t), list) else None
            if not names:
                skipped[t or "unknown"] = skipped.get(t or "unknown", 0) + 1
                continue
            label = str(names[0])
            if t == "rectanglelabels":
                if abs(float(v.get("rotation", 0))) > 0.01:
                    rotated += 1
                shape = Shape(label, "bbox", {"kind": "bbox", "x": v["x"] / 100, "y": v["y"] / 100, "w": v["width"] / 100, "h": v["height"] / 100}, normalized=True)
            elif t == "polygonlabels":
                pts = [[p[0] / 100, p[1] / 100] for p in v.get("points", [])]
                shape = Shape(label, "polygon", {"kind": "region", "polygons": [[closed(pts)]]}, normalized=True)
            elif t == "keypointlabels":
                shape = Shape(label, "point", {"kind": "point", "x": v["x"] / 100, "y": v["y"] / 100}, normalized=True)
            else:
                skipped[t] = skipped.get(t, 0) + 1
                continue
            entry.shapes.append(shape)
            entry.explicit_empty = False
            if label not in labels:
                labels.append(label)
        entries.append(entry)
    if not entries:
        raise FormatError("The Label Studio export has no tasks with images.")
    warnings = [f"{n} {t} result(s) are not supported yet and were skipped." for t, n in skipped.items()]
    if rotated:
        warnings.append(f"{rotated} rotated box(es) were imported without their rotation.")
    return Parsed("label_studio", entries, labels, warnings)


def write(images: list[ExportImage], labels: list[str]) -> tuple[bytes, list[str]]:
    del labels  # Label Studio stores label names on each result
    holes = 0
    tasks = []
    files: dict[str, bytes] = {}
    for idx, im in enumerate(images, start=1):
        W, H = im.width, im.height
        result = []
        for s in im.shapes:
            g = s.geometry
            base = {"from_name": "label", "to_name": "image", "original_width": W, "original_height": H, "image_rotation": 0}
            if s.shape == "bbox":
                result.append({**base, "id": _rid(), "type": "rectanglelabels", "value": {
                    "x": g["x"] / W * 100, "y": g["y"] / H * 100, "width": g["w"] / W * 100, "height": g["h"] / H * 100,
                    "rotation": 0, "rectanglelabels": [s.label]}})
            elif s.shape == "polygon":
                holes += sum(len(p) - 1 for p in g["polygons"])
                for ring in ring_points(g):
                    result.append({**base, "id": _rid(), "type": "polygonlabels", "value": {
                        "points": [[x / W * 100, y / H * 100] for x, y in open_ring(ring)], "closed": True, "polygonlabels": [s.label]}})
            else:
                result.append({**base, "id": _rid(), "type": "keypointlabels", "value": {
                    "x": g["x"] / W * 100, "y": g["y"] / H * 100, "width": 0.5, "keypointlabels": [s.label]}})
        tasks.append({"id": idx, "data": {"image": f"/data/upload/{im.file_name}"}, "annotations": [{"id": idx, "result": result}]})
        if im.data is not None:
            files[f"images/{im.file_name}"] = im.data
    files["label_studio.json"] = json.dumps(tasks).encode()
    notes = [f"{holes} hole(s) inside outlines were dropped: Label Studio polygons can't store holes."] if holes else []
    return zip_bytes(files), notes


def _rid() -> str:
    return uuid.uuid4().hex[:10]
