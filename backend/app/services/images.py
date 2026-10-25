"""
Everything that happens to an image file when it enters the system (upload, import, seed):
decode, apply EXIF rotation, hash, thumbnail, store, and tile it if it is large.
"""

from __future__ import annotations

import hashlib
import io
import uuid

from PIL import Image, ImageOps, UnidentifiedImageError

from app.config import get_settings
from app.services import tiling

Image.MAX_IMAGE_PIXELS = 1_000_000_000  # large scans are allowed; they are tiled
BROWSER_FORMATS = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
EXT = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/tiff": "tif"}


def dhash(img: Image.Image) -> str:
    """64-bit difference hash: survives renaming, re-saving and mild compression."""
    small = img.convert("L").resize((9, 8), Image.LANCZOS)
    px = list(small.tobytes())  # one byte per pixel in "L" mode
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (px[row * 9 + col] > px[row * 9 + col + 1])
    return f"{bits:016x}"


def analyze(data: bytes) -> dict:
    """Decode, orient, hash and thumbnail. Raises ValueError for unreadable files."""
    try:
        img = Image.open(io.BytesIO(data))
        fmt = img.format
        img.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("not a readable image") from exc
    oriented = ImageOps.exif_transpose(img)
    rgb = oriented.convert("RGB")
    large = max(rgb.size) > get_settings().tile_threshold_px
    display_bytes, display_type = data, BROWSER_FORMATS.get(fmt or "")
    if (display_type is None or oriented is not img) and not large:
        # TIFF/BMP etc. (or rotated by EXIF): a PNG the browser shows exactly as measured.
        # Large images skip this: the browser sees tiles, and the original stays as-is.
        buf = io.BytesIO()
        rgb.save(buf, "PNG")
        display_bytes, display_type = buf.getvalue(), "image/png"
    elif display_type is None:
        display_type = "image/tiff" if fmt == "TIFF" else "application/octet-stream"
    thumb = rgb.copy()
    thumb.thumbnail((320, 240))
    tbuf = io.BytesIO()
    thumb.save(tbuf, "JPEG", quality=82)
    return {
        "width": rgb.width,
        "height": rgb.height,
        "sha256": hashlib.sha256(data).hexdigest(),
        "phash": dhash(rgb),
        "display": display_bytes,
        "display_type": display_type,
        "thumb": tbuf.getvalue(),
        "format": fmt,
        "large": large,
        "_rgb": rgb,
    }


def store(storage, project_id: uuid.UUID, item_id: uuid.UUID, info: dict) -> dict:
    """Writes the image, its thumbnail and (if large) its tile pyramid. Returns Item fields."""
    base = f"projects/{project_id}/items/{item_id}"
    key = f"{base}/image.{EXT.get(info['display_type'], 'bin')}"
    thumb_key = f"{base}/thumb.jpg"
    storage.put(key, info["display"], info["display_type"])
    storage.put(thumb_key, info["thumb"], "image/jpeg")
    fields = {
        "storage_key": key, "thumb_key": thumb_key, "width": info["width"], "height": info["height"],
        "sha256": info["sha256"], "phash": info["phash"], "size_bytes": len(info["display"]),
        "is_tiled": False, "tiles_prefix": None, "tile_size": None, "tile_format": None,
    }
    if info["large"]:
        s = get_settings()
        prefix = f"{base}/tiles"
        n = tiling.write_pyramid(storage, prefix, info["_rgb"], s.tile_size, "jpeg")
        fields.update(is_tiled=True, tiles_prefix=prefix, tile_size=s.tile_size, tile_format="jpeg")
        fields["tile_count"] = n
    return fields
