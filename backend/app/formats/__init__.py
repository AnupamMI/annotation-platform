"""Detect an upload's format and read it; write exports in any supported format."""

from __future__ import annotations

from app.formats import coco, cvat, labelstudio, yolo
from app.formats.common import Archive, ExportImage, FormatError, ImageEntry, Parsed, Shape

FORMATS = {"yolo": yolo, "coco": coco, "cvat_xml": cvat, "label_studio": labelstudio}
FORMAT_NAMES = {"yolo": "YOLO", "coco": "COCO JSON", "cvat_xml": "CVAT XML", "label_studio": "Label Studio JSON"}

__all__ = ["Archive", "ExportImage", "FormatError", "ImageEntry", "Parsed", "Shape", "detect", "read_upload", "write_export", "FORMATS", "FORMAT_NAMES"]


def detect(archive: Archive) -> str:
    """Decided by file structure, not by asking a model: it has to be exact."""
    if cvat.find(archive):
        return "cvat_xml"
    if coco.find(archive):
        return "coco"
    if labelstudio.find(archive):
        return "label_studio"
    if yolo.looks_like(archive):
        return "yolo"
    raise FormatError(
        "The format wasn't recognised. Supported: CVAT XML (for images 1.1), COCO JSON, Label Studio JSON, "
        "or a YOLO folder zipped with its .txt label files and data.yaml."
    )


def read_upload(name: str, data: bytes) -> Parsed:
    archive = Archive(name, data)
    return FORMATS[detect(archive)].read(archive)


def write_export(fmt: str, images: list[ExportImage], labels: list[str]) -> tuple[bytes, list[str]]:
    if fmt not in FORMATS:
        raise FormatError(f"Unknown export format: {fmt}")
    return FORMATS[fmt].write(images, labels)
