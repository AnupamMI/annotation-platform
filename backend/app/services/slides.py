"""
Whole-slide images (Aperio .svs, Hamamatsu .ndpi, Leica .scn, Ventana .bif, pyramidal TIFF).

Slides are far too big to decode in memory, so they are handled differently from
ordinary images:
- the original file is stored as-is (streamed, never fully loaded);
- width, height, microns per pixel and magnification are read from the file;
- Deep Zoom tiles are made on demand from the slide's own pyramid the first time
  they are viewed, then kept in storage, so later views are instant;
- the label and macro photos inside the slide file are never extracted or shown,
  because the label can carry patient details.

Reading uses OpenSlide (pip: openslide-python + openslide-bin).
"""

from __future__ import annotations

import hashlib
import io
import os
import tempfile
import threading
import uuid
from collections import OrderedDict
from pathlib import Path

from app.config import get_settings
from app.services import tiling

SLIDE_EXT = {".svs", ".ndpi", ".scn", ".bif", ".svslide", ".vms", ".tif", ".tiff"}
OPEN_LIMIT = 8  # slides kept open at once


def _openslide():
    try:
        import openslide
        from openslide.deepzoom import DeepZoomGenerator
    except Exception as exc:  # library or its native part missing
        raise RuntimeError("Whole-slide support needs OpenSlide: pip install openslide-python openslide-bin") from exc
    return openslide, DeepZoomGenerator


def is_slide(file_name: str, path: str) -> bool:
    """True for files OpenSlide reads as a multi-resolution slide. Plain TIFFs stay ordinary images."""
    if Path(file_name).suffix.lower() not in SLIDE_EXT:
        return False
    try:
        openslide, _ = _openslide()
    except RuntimeError:
        return Path(file_name).suffix.lower() not in {".tif", ".tiff"}  # report the missing library for real slide types
    fmt = openslide.OpenSlide.detect_format(path)
    if fmt is None:
        return False
    if fmt != "generic-tiff":
        return True
    with openslide.OpenSlide(path) as s:
        return s.level_count > 1


def analyze(path: str) -> dict:
    """Reads size, scale and a thumbnail without loading the slide. Raises ValueError if unreadable."""
    from app.services.images import dhash

    openslide, _ = _openslide()
    try:
        s = openslide.OpenSlide(path)
    except openslide.OpenSlideError as exc:
        raise ValueError("not a readable slide") from exc
    with s:
        w, h = s.dimensions
        p = s.properties
        thumb = s.get_thumbnail((320, 240)).convert("RGB")
        hash_img = s.get_thumbnail((256, 256)).convert("RGB")
        meta = {
            "vendor": p.get("openslide.vendor"),
            "mpp_x": _num(p.get("openslide.mpp-x")),
            "mpp_y": _num(p.get("openslide.mpp-y")),
            "objective": _num(p.get("openslide.objective-power")),
            "levels": [list(d) for d in s.level_dimensions],
        }
    tbuf = io.BytesIO()
    thumb.save(tbuf, "JPEG", quality=82)
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return {"width": w, "height": h, "sha256": digest.hexdigest(), "phash": dhash(hash_img), "thumb": tbuf.getvalue(),
            "slide": meta, "size_bytes": os.path.getsize(path)}


def store(storage, project_id: uuid.UUID, item_id: uuid.UUID, path: str, file_name: str, info: dict) -> dict:
    base = f"projects/{project_id}/items/{item_id}"
    key = f"{base}/slide{Path(file_name).suffix.lower()}"
    thumb_key = f"{base}/thumb.jpg"
    storage.put_file(key, path, "application/octet-stream")
    storage.put(thumb_key, info["thumb"], "image/jpeg")
    # keep the local copy as the tile cache, so the first view doesn't download it again
    cached = _cache_path(item_id, key)
    if not cached.exists():
        try:
            os.link(path, cached)
        except OSError:
            import shutil

            shutil.copyfile(path, cached)
    return {
        "storage_key": key, "thumb_key": thumb_key, "width": info["width"], "height": info["height"],
        "sha256": info["sha256"], "phash": info["phash"], "size_bytes": info["size_bytes"],
        "is_tiled": True, "tiles_prefix": f"{base}/tiles", "tile_size": get_settings().tile_size, "tile_format": "jpeg",
    }


# ---------------- tiles on demand ----------------
_open: OrderedDict[uuid.UUID, tuple] = OrderedDict()
_lock = threading.Lock()


def _cache_dir() -> Path:
    d = Path(get_settings().slide_cache_dir or Path(tempfile.gettempdir()) / "annot-slides")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cache_path(item_id: uuid.UUID, key: str) -> Path:
    return _cache_dir() / f"{item_id}{Path(key).suffix}"


def _generator(storage, item_id: uuid.UUID, key: str, tile_size: int):
    with _lock:
        if item_id in _open:
            _open.move_to_end(item_id)
            return _open[item_id][1]
    openslide, DeepZoomGenerator = _openslide()
    path = _cache_path(item_id, key)
    if not path.exists():
        tmp = path.with_suffix(path.suffix + ".part")
        storage.get_file(key, str(tmp))
        tmp.replace(path)
    slide = openslide.OpenSlide(str(path))
    gen = DeepZoomGenerator(slide, tile_size=tile_size, overlap=tiling.OVERLAP, limit_bounds=False)
    with _lock:
        _open[item_id] = (slide, gen)
        while len(_open) > OPEN_LIMIT:
            _, (old, _) = _open.popitem(last=False)
            old.close()
    return gen


def tile(storage, item_id: uuid.UUID, key: str, tile_size: int, level: int, col: int, row: int) -> bytes:
    """One Deep Zoom tile, read from the slide. Raises KeyError for a tile outside the image."""
    gen = _generator(storage, item_id, key, tile_size)
    if not 0 <= level < gen.level_count:
        raise KeyError("level")
    cols, rows = gen.level_tiles[level]
    if not (0 <= col < cols and 0 <= row < rows):
        raise KeyError("tile")
    img = gen.get_tile(level, (col, row)).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    return buf.getvalue()


def warm(storage, item_id: uuid.UUID, key: str, tile_size: int, tiles_prefix: str, max_side: int = 4096) -> int:
    """
    Makes all overview tiles ahead of time, so a slide opens instantly.

    Tile by tile, every overview level would decode the same low-resolution layer of
    the slide again (slow for JPEG 2000 slides). Instead the layer is decoded once,
    resized to the largest Deep Zoom level that fits in max_side, and that image is cut
    into that level and every smaller one. Deeper levels are made on demand.
    """
    try:
        gen = _generator(storage, item_id, key, tile_size)
        with _lock:
            slide = _open[item_id][0]
        top = max((lv for lv in range(gen.level_count) if max(gen.level_dimensions[lv]) <= max_side), default=None)
        if top is None:
            return 0
        w, h = gen.level_dimensions[top]
        full_w = slide.dimensions[0]
        best = slide.get_best_level_for_downsample(full_w / w * 1.01)  # stored layers are often a hair off 2^n
        lw, lh = slide.level_dimensions[best]
        if lw * lh > 80_000_000:  # no small enough layer in this file; leave it to on-demand tiles
            return 0
        from PIL import Image

        layer = slide.read_region((0, 0), best, (lw, lh))
        bg = Image.new("RGB", layer.size, "white")
        bg.paste(layer, mask=layer.split()[3])
        img = bg.resize((w, h), Image.LANCZOS) if (lw, lh) != (w, h) else bg
        n = 0
        for level, col, row, data in tiling.tiles(img, tile_size, "jpeg"):
            storage.put(f"{tiles_prefix}/{level}/{col}_{row}.jpeg", data, "image/jpeg")
            n += 1
        return n
    except Exception as exc:  # best effort: tiles are still made on demand
        import logging

        logging.getLogger("slides").warning("Overview tiles not made ahead: %s", exc)
        return 0


def _num(v) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None
