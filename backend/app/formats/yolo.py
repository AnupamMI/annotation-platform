"""
YOLO (Ultralytics) detection and segmentation labels.

Layout: data.yaml (or classes.txt / obj.names) with class names, one .txt per image:
    <class> <cx> <cy> <w> <h>                 box, normalized 0-1
    <class> <x1> <y1> <x2> <y2> ... <xn> <yn>  polygon, normalized 0-1
An empty .txt means "annotated, no objects". An image without a .txt is ambiguous.
"""

from __future__ import annotations

from pathlib import PurePosixPath

import yaml

from app.formats.common import (
    Archive,
    ExportImage,
    FormatError,
    ImageEntry,
    Parsed,
    Shape,
    closed,
    open_ring,
    ring_points,
    zip_bytes,
)

NAME_FILES = ("classes.txt", "obj.names", "classes.names")
NOT_LABELS = {"classes.txt", "obj.names", "classes.names", "readme.txt", "license.txt", "manifest.txt"}


def _names(archive: Archive) -> list[str] | None:
    for path, data in archive.by_suffix(".yaml", ".yml").items():
        try:
            doc = yaml.safe_load(data.decode("utf-8", "replace"))
        except yaml.YAMLError:
            continue
        names = doc.get("names") if isinstance(doc, dict) else None
        if isinstance(names, list):
            return [str(n) for n in names]
        if isinstance(names, dict):
            return [str(names[k]) for k in sorted(names, key=lambda k: int(k))]
    for path, data in archive.files.items():
        if PurePosixPath(path).name.lower() in NAME_FILES:
            return [l.strip() for l in data.decode("utf-8", "replace").splitlines() if l.strip()]
    return None


def looks_like(archive: Archive) -> bool:
    """Checked after the JSON/XML formats, so other files (e.g. manifest.json) don't matter here."""
    return any(PurePosixPath(p).name.lower() not in NOT_LABELS for p in archive.by_suffix(".txt"))


def read(archive: Archive) -> Parsed:
    warnings: list[str] = []
    names = _names(archive)
    if names is None:
        warnings.append("No class names found (data.yaml or classes.txt). Labels are shown as class numbers.")
    label_files = {
        PurePosixPath(p).stem.lower(): (p, b)
        for p, b in archive.by_suffix(".txt").items()
        if PurePosixPath(p).name.lower() not in NOT_LABELS
    }
    images = archive.images()
    image_by_stem = {PurePosixPath(n).stem: (n, b) for n, b in images.items()}
    used: set[int] = set()
    entries: list[ImageEntry] = []
    bad_lines = 0

    for stem, (path, data) in sorted(label_files.items()):
        img_name, img_bytes = image_by_stem.get(stem, (None, None))
        entry = ImageEntry(file_name=img_name or f"{PurePosixPath(path).stem}", data=img_bytes)
        lines = [l.split() for l in data.decode("utf-8", "replace").splitlines() if l.strip()]
        entry.explicit_empty = not lines
        for parts in lines:
            try:
                cls = int(float(parts[0]))
                nums = [float(v) for v in parts[1:]]
            except (ValueError, IndexError):
                bad_lines += 1
                continue
            used.add(cls)
            label = names[cls] if names and 0 <= cls < len(names) else str(cls)
            if len(nums) in (4, 5):  # box (5 = box + confidence from a prediction file)
                cx, cy, w, h = nums[:4]
                entry.shapes.append(Shape(label, "bbox", {"kind": "bbox", "x": cx - w / 2, "y": cy - h / 2, "w": w, "h": h}, normalized=True))
            elif len(nums) >= 6 and len(nums) % 2 == 0:
                pts = [[nums[i], nums[i + 1]] for i in range(0, len(nums), 2)]
                entry.shapes.append(Shape(label, "polygon", {"kind": "region", "polygons": [[closed(pts)]]}, normalized=True))
            else:
                bad_lines += 1
        entries.append(entry)

    for name, data in images.items():
        if PurePosixPath(name).stem not in label_files:
            entries.append(ImageEntry(file_name=name, data=data, ambiguous_empty=True))

    if not entries:
        raise FormatError("No YOLO label files (.txt) were found.")
    if bad_lines:
        warnings.append(f"{bad_lines} label line(s) could not be read and were skipped.")
    labels = names if names else [str(c) for c in sorted(used)]
    return Parsed("yolo", entries, labels, warnings)


def write(images: list[ExportImage], labels: list[str]) -> tuple[bytes, list[str]]:
    index = {name: i for i, name in enumerate(labels)}
    files: dict[str, bytes] = {}
    notes: list[str] = []
    skipped_points = 0
    splits = sorted({im.split for im in images if im.split})
    for im in images:
        sub = f"{im.split}/" if im.split else ""
        lines = []
        for s in im.shapes:
            c = index[s.label]
            g = s.geometry
            if s.shape == "bbox":
                lines.append(f"{c} {(g['x'] + g['w'] / 2) / im.width:.6f} {(g['y'] + g['h'] / 2) / im.height:.6f} {g['w'] / im.width:.6f} {g['h'] / im.height:.6f}")
            elif s.shape == "polygon":
                for ring in ring_points(g):  # each part of a region becomes its own polygon
                    coords = " ".join(f"{x / im.width:.6f} {y / im.height:.6f}" for x, y in open_ring(ring))
                    lines.append(f"{c} {coords}")
            else:
                skipped_points += 1
        stem = PurePosixPath(im.file_name).stem
        files[f"labels/{sub}{stem}.txt"] = ("\n".join(lines) + ("\n" if lines else "")).encode()
        if im.data is not None:
            files[f"images/{sub}{im.file_name}"] = im.data
    data_yaml = {"path": ".", "names": {i: n for i, n in enumerate(labels)}}
    if splits:
        for sp in splits:
            data_yaml[sp] = f"images/{sp}"
    else:
        data_yaml["train"] = "images"
    files["data.yaml"] = yaml.safe_dump(data_yaml, sort_keys=False, allow_unicode=True).encode()
    if skipped_points:
        notes.append(f"{skipped_points} point annotation(s) were left out: YOLO detection/segmentation has no point shape.")
    return zip_bytes(files), notes
