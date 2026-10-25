"""
Deep Zoom (DZI) tile pyramids for large images.

Level L of a W x H image is W / 2^(max-L) wide, where max = ceil(log2(max(W, H))).
Each level is cut into tile_size tiles with a 1 px overlap, the layout OpenSeadragon
expects. Levels are made by halving the previous level, so memory stays near the
size of the full image.
"""

from __future__ import annotations

import io
import math
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor

from PIL import Image

OVERLAP = 1


def levels(width: int, height: int) -> int:
    return math.ceil(math.log2(max(width, height))) + 1


def tiles(img: Image.Image, tile_size: int, fmt: str = "jpeg", quality: int = 88) -> Iterator[tuple[int, int, int, bytes]]:
    """Yields (level, col, row, encoded tile), from the full-resolution level down to 1x1."""
    max_level = levels(*img.size) - 1
    current = img.convert("RGB")
    for level in range(max_level, -1, -1):
        w, h = current.size
        cols, rows = math.ceil(w / tile_size), math.ceil(h / tile_size)
        for col in range(cols):
            for row in range(rows):
                x0 = col * tile_size - (OVERLAP if col else 0)
                y0 = row * tile_size - (OVERLAP if row else 0)
                x1 = min(col * tile_size + tile_size + OVERLAP, w)
                y1 = min(row * tile_size + tile_size + OVERLAP, h)
                buf = io.BytesIO()
                current.crop((x0, y0, x1, y1)).save(buf, "JPEG" if fmt == "jpeg" else "PNG", quality=quality)
                yield level, col, row, buf.getvalue()
        if level:
            current = current.resize((max(1, math.ceil(w / 2)), max(1, math.ceil(h / 2))), Image.LANCZOS)


def dzi_xml(width: int, height: int, tile_size: int, fmt: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<Image xmlns="http://schemas.microsoft.com/deepzoom/2008" TileSize="{tile_size}" Overlap="{OVERLAP}" Format="{fmt}">'
        f'<Size Width="{width}" Height="{height}"/></Image>'
    )


def write_pyramid(storage, prefix: str, img: Image.Image, tile_size: int, fmt: str = "jpeg") -> int:
    """Encodes and uploads every tile; storage writes run in parallel. Returns the tile count."""
    ext = "jpeg" if fmt == "jpeg" else "png"
    content_type = "image/jpeg" if fmt == "jpeg" else "image/png"
    count = 0
    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = []
        for level, col, row, data in tiles(img, tile_size, fmt):
            futures.append(pool.submit(storage.put, f"{prefix}/{level}/{col}_{row}.{ext}", data, content_type))
            count += 1
            if len(futures) > 256:  # keep memory bounded on very large images
                for f in futures:
                    f.result()
                futures = []
        for f in futures:
            f.result()
    return count
