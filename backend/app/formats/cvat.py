"""
CVAT for images 1.1 (XML). <image name width height> with <box>, <polygon> and
<points> children. Polylines, masks and video tracks are reported, not read.
"""

from __future__ import annotations

from xml.sax.saxutils import quoteattr

from defusedxml import ElementTree as ET

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


def _points(text: str) -> list[list[float]]:
    out = []
    for pair in text.strip().split(";"):
        if pair.strip():
            x, y = pair.split(",")
            out.append([float(x), float(y)])
    return out


def find(archive: Archive):
    for path, data in archive.by_suffix(".xml").items():
        try:
            root = ET.fromstring(data)
        except ET.ParseError:
            continue
        if root.tag == "annotations":
            return path, root
    return None


def read(archive: Archive) -> Parsed:
    found = find(archive)
    if not found:
        raise FormatError("No CVAT XML (<annotations>) was found.")
    _, root = found
    images = archive.images()
    labels: list[str] = [el.findtext("name", "") for el in root.iter("label") if el.findtext("name")]
    entries: list[ImageEntry] = []
    skipped: dict[str, int] = {}
    for im in root.iter("image"):
        name = basename(im.get("name", ""))
        entry = ImageEntry(
            file_name=name, width=int(float(im.get("width", 0))) or None, height=int(float(im.get("height", 0))) or None,
            data=images.get(name.lower()), explicit_empty=True,
        )
        for el in im:
            label = el.get("label")
            if not label:
                continue
            if el.tag == "box":
                x0, y0, x1, y1 = (float(el.get(k)) for k in ("xtl", "ytl", "xbr", "ybr"))
                entry.shapes.append(Shape(label, "bbox", {"kind": "bbox", "x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}))
            elif el.tag == "polygon":
                entry.shapes.append(Shape(label, "polygon", {"kind": "region", "polygons": [[closed(_points(el.get("points", "")))]]}))
            elif el.tag == "points":
                for x, y in _points(el.get("points", "")):
                    entry.shapes.append(Shape(label, "point", {"kind": "point", "x": x, "y": y}))
            else:
                skipped[el.tag] = skipped.get(el.tag, 0) + 1
                continue
            if label not in labels:
                labels.append(label)
            entry.explicit_empty = False
        entries.append(entry)
    if not entries:
        if any(True for _ in root.iter("track")):
            raise FormatError("This is a CVAT video export (tracks). Export as 'CVAT for images 1.1' instead.")
        raise FormatError("The CVAT XML has no <image> entries.")
    warnings = [f"{n} {tag} annotation(s) are not supported yet and were skipped." for tag, n in skipped.items()]
    return Parsed("cvat_xml", entries, labels, warnings)


def write(images: list[ExportImage], labels: list[str]) -> tuple[bytes, list[str]]:
    holes = 0
    out = ['<?xml version="1.0" encoding="utf-8"?>', "<annotations>", "  <version>1.1</version>", "  <meta><task><labels>"]
    for n in labels:
        out.append(f"    <label><name>{_esc(n)}</name></label>")
    out.append("  </labels></task></meta>")
    files: dict[str, bytes] = {}
    for idx, im in enumerate(images):
        out.append(f'  <image id="{idx}" name={quoteattr(im.file_name)} width="{im.width}" height="{im.height}"' + (f' subset="{im.split}"' if im.split else "") + ">")
        for s in im.shapes:
            g, lab = s.geometry, quoteattr(s.label)
            if s.shape == "bbox":
                out.append(f'    <box label={lab} source="manual" occluded="0" xtl="{g["x"]:.2f}" ytl="{g["y"]:.2f}" xbr="{g["x"] + g["w"]:.2f}" ybr="{g["y"] + g["h"]:.2f}" z_order="0"/>')
            elif s.shape == "polygon":
                holes += sum(len(p) - 1 for p in g["polygons"])
                for ring in ring_points(g):
                    pts = ";".join(f"{x:.2f},{y:.2f}" for x, y in open_ring(ring))
                    out.append(f'    <polygon label={lab} source="manual" occluded="0" points="{pts}" z_order="0"/>')
            else:
                out.append(f'    <points label={lab} source="manual" occluded="0" points="{g["x"]:.2f},{g["y"]:.2f}" z_order="0"/>')
        out.append("  </image>")
        if im.data is not None:
            files[f"images/{im.file_name}"] = im.data
    out.append("</annotations>")
    files["annotations.xml"] = "\n".join(out).encode()
    notes = [f"{holes} hole(s) inside outlines were dropped: CVAT polygons can't store holes."] if holes else []
    return zip_bytes(files), notes


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
