"""
Annotation file formats: shared in-memory representation.

Every reader turns an upload into a `Parsed` object; every writer turns a list of
`ExportImage` objects into archive bytes. Coordinates are full-image pixels, except
YOLO, whose readers return normalized coordinates (marked `normalized=True`) until
the image size is known.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePosixPath

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
MAX_ARCHIVE_BYTES = 4 * 1024**3  # uncompressed, guards against zip bombs
MAX_ARCHIVE_MEMBERS = 200_000


class FormatError(ValueError):
    """The upload could not be read; the message is shown to the user."""


@dataclass
class Shape:
    label: str
    shape: str  # "bbox" | "polygon" | "point"
    geometry: dict  # API geometry dict (see app.geo)
    normalized: bool = False  # True for YOLO until resolved against the image size


@dataclass
class ImageEntry:
    file_name: str  # as named in the export (basename)
    width: int | None = None  # size the annotations were made at, if the export says
    height: int | None = None
    shapes: list[Shape] = field(default_factory=list)
    data: bytes | None = None  # image bytes, when the archive contains the image
    explicit_empty: bool = False  # the export says "annotated, no objects"
    ambiguous_empty: bool = False  # e.g. YOLO image without a label file


@dataclass
class Parsed:
    format: str  # "yolo" | "coco" | "cvat_xml" | "label_studio"
    images: list[ImageEntry]
    labels: list[str]  # in export order (class index order for YOLO)
    warnings: list[str] = field(default_factory=list)


@dataclass
class ExportImage:
    file_name: str  # unique within the export
    width: int
    height: int
    shapes: list[Shape]
    data: bytes | None = None
    split: str | None = None  # "train" | "val" | "test" for datasets


# ---------------- archive helpers ----------------
class Archive:
    """Read-only view of an upload: a zip, or a single JSON/XML/TXT file."""

    def __init__(self, name: str, data: bytes):
        self.name = name
        self.files: dict[str, bytes] = {}
        if zipfile.is_zipfile(io.BytesIO(data)):
            try:
                zf = zipfile.ZipFile(io.BytesIO(data))
            except zipfile.BadZipFile as exc:
                raise FormatError("The zip file is damaged.") from exc
            infos = [i for i in zf.infolist() if not i.is_dir()]
            if len(infos) > MAX_ARCHIVE_MEMBERS:
                raise FormatError("The archive has too many files.")
            if sum(i.file_size for i in infos) > MAX_ARCHIVE_BYTES:
                raise FormatError("The archive is too large once unpacked.")
            for info in infos:
                path = info.filename.replace("\\", "/")
                parts = PurePosixPath(path).parts
                if any(p.startswith(".") or p == "__MACOSX" for p in parts):
                    continue  # hidden files and macOS resource forks
                self.files[path] = zf.read(info)
        else:
            self.files[PurePosixPath(name).name] = data

    def by_suffix(self, *suffixes: str) -> dict[str, bytes]:
        return {p: b for p, b in self.files.items() if PurePosixPath(p).suffix.lower() in suffixes}

    def images(self) -> dict[str, bytes]:
        """Image files by lowercase basename (and stem, for formats that drop extensions)."""
        out: dict[str, bytes] = {}
        for p, b in self.by_suffix(*IMAGE_EXTS).items():
            out[PurePosixPath(p).name.lower()] = b
        return out


def basename(path_or_url: str) -> str:
    return PurePosixPath(path_or_url.split("?")[0].replace("\\", "/")).name


def image_size(data: bytes) -> tuple[int, int]:
    """Size as displayed (after EXIF rotation), which is how annotation tools show it."""
    from PIL import Image

    img = Image.open(io.BytesIO(data))
    w, h = img.size
    try:
        orientation = img.getexif().get(0x0112, 1)
    except Exception:
        orientation = 1
    if orientation in (5, 6, 7, 8):
        w, h = h, w
    return w, h


def ring_points(geometry: dict) -> list[list[list[float]]]:
    """Outer rings of a region (holes are dropped by formats that can't store them)."""
    return [poly[0] for poly in geometry["polygons"]]


def open_ring(ring: list) -> list:
    """Drop the closing point (most formats list each vertex once)."""
    if len(ring) > 1 and ring[0][0] == ring[-1][0] and ring[0][1] == ring[-1][1]:
        return ring[:-1]
    return ring


def closed(points: list) -> list:
    pts = [[float(x), float(y)] for x, y in points]
    if pts and pts[0] != pts[-1]:
        pts.append(list(pts[0]))
    return pts


def zip_bytes(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, data in files.items():
            # images are already compressed; storing them is faster and no bigger
            method = zipfile.ZIP_STORED if PurePosixPath(path).suffix.lower() in IMAGE_EXTS else zipfile.ZIP_DEFLATED
            zf.writestr(path, data, compress_type=method)
    return buf.getvalue()
