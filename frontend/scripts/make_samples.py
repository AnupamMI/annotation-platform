"""
Generate SYNTHETIC Pap-smear-like field-of-view images for the UI prototype.

No real patient data. Rendering uses a simple Beer-Lambert absorption model:
each stain adds optical density per RGB channel, so overlapping cells darken
naturally, the way stained material does under brightfield light.

- Squamous cells: wavy polygonal outlines, eosinophilic (pink) or cyanophilic
  (green-blue) cytoplasm with fine texture.
- Nuclei: small and pale-violet in normal cells; enlarged and hyperchromatic in a
  few "abnormal" cells.
- Scattered small inflammatory cells.

Also writes samples.json with the cell and nucleus outlines, used as
pre-existing annotations on some images.

Run:  python scripts/make_samples.py
"""

import json
import math
import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

OUT = Path(__file__).resolve().parent.parent / "public" / "samples"
W, H = 1600, 1200
N_IMAGES = 12

# Optical density per unit stain, per RGB channel (higher = absorbs more of that channel)
OD_EOSIN = np.array([0.08, 0.50, 0.22], np.float32)      # pink cytoplasm
OD_CYANO = np.array([0.42, 0.14, 0.10], np.float32)      # green-blue cytoplasm
OD_HEMA = np.array([0.95, 1.05, 0.40], np.float32)       # nuclei, violet-blue


def smooth_noise(h, w, scale, rng):
    """Low-frequency noise in [0, 1]."""
    small = rng.random((max(2, h // scale), max(2, w // scale))).astype(np.float32)
    img = Image.fromarray((small * 255).astype(np.uint8)).resize((w, h), Image.BICUBIC)
    return np.asarray(img, np.float32) / 255.0


def wavy_outline(cx, cy, r, rng, n=64, lobes=None, jitter=0.16):
    lobes = lobes or rng.randint(5, 8)
    harmonics = [(k, rng.uniform(0, jitter / k ** 0.6), rng.uniform(0, 2 * math.pi)) for k in range(2, lobes + 1)]
    squash = rng.uniform(0.78, 1.0)
    rot = rng.uniform(0, math.pi)
    pts = []
    for i in range(n):
        a = 2 * math.pi * i / n
        rr = r * (1 + sum(amp * math.sin(k * a + ph) for k, amp, ph in harmonics))
        x, y = rr * math.cos(a), rr * math.sin(a) * squash
        pts.append((cx + x * math.cos(rot) - y * math.sin(rot), cy + x * math.sin(rot) + y * math.cos(rot)))
    return pts


def mask_for(poly, x0, y0, w, h, blur):
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).polygon([(x - x0, y - y0) for x, y in poly], fill=255)
    if blur:
        m = m.filter(ImageFilter.GaussianBlur(blur))
    return np.asarray(m, np.float32) / 255.0


def add_density(od, poly, stain, strength, texture_scale, texture_amt, blur, nprng, edge_boost=0.0):
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    pad = int(blur * 3) + 2
    x0, y0 = max(0, int(min(xs)) - pad), max(0, int(min(ys)) - pad)
    x1, y1 = min(W, int(max(xs)) + pad), min(H, int(max(ys)) + pad)
    w, h = x1 - x0, y1 - y0
    if w <= 2 or h <= 2:
        return
    m = mask_for(poly, x0, y0, w, h, blur)
    tex = 1 - texture_amt + texture_amt * smooth_noise(h, w, texture_scale, nprng)
    density = m * tex * strength
    if edge_boost:
        inner = mask_for(poly, x0, y0, w, h, blur * 3)
        density += np.clip(m - inner, 0, 1) * edge_boost
    od[y0:y1, x0:x1] += density[..., None] * stain


def render(idx, rng, nprng):
    y, x = np.mgrid[0:H, 0:W].astype(np.float32)
    # brightfield illumination: slightly warm-grey, uneven, with vignette
    gx = x / W - 0.5 + rng.uniform(-0.12, 0.12)
    gy = y / H - 0.5 + rng.uniform(-0.12, 0.12)
    light = (1 - 0.32 * (gx ** 2 + gy ** 2))[..., None] * np.array([0.95, 0.94, 0.96], np.float32)
    od = np.zeros((H, W, 3), np.float32)
    # faint background haze (mucus / proteinaceous material)
    od += (smooth_noise(H, W, 90, nprng) * 0.035)[..., None] * OD_CYANO

    cells, placed = [], []
    n_cells = rng.randint(16, 26)
    abnormal_left = rng.choice([0, 1, 1, 2, 3])
    tries = 0
    while len(cells) < n_cells and tries < 900:
        tries += 1
        r = rng.uniform(58, 112)
        cx, cy = rng.uniform(r * 0.6, W - r * 0.6), rng.uniform(r * 0.6, H - r * 0.6)
        if any(math.hypot(cx - px, cy - py) < (r + pr) * 0.62 for px, py, pr in placed):
            continue
        placed.append((cx, cy, r))
        poly = wavy_outline(cx, cy, r, rng)
        stain = OD_EOSIN if rng.random() < 0.42 else OD_CYANO
        add_density(od, poly, stain, rng.uniform(0.32, 0.5), rng.choice([14, 20, 28]), 0.45, 1.4, nprng,
                    edge_boost=0.14)

        abnormal = abnormal_left > 0 and rng.random() < 0.3
        if abnormal:
            abnormal_left -= 1
            nr = r * rng.uniform(0.2, 0.3)
            npoly = wavy_outline(cx + rng.uniform(-8, 8), cy + rng.uniform(-8, 8), nr, rng, n=40, jitter=0.12)
            add_density(od, npoly, OD_HEMA, rng.uniform(1.15, 1.45), 3, 0.55, 0.9, nprng)
        else:
            nr = rng.uniform(6.5, 10.5)
            npoly = wavy_outline(cx + rng.uniform(-r * 0.12, r * 0.12), cy + rng.uniform(-r * 0.12, r * 0.12),
                                 nr, rng, n=28, jitter=0.08)
            add_density(od, npoly, OD_HEMA, rng.uniform(0.7, 0.95), 2, 0.35, 0.8, nprng)
        cells.append({
            "cell": [[round(px, 1), round(py, 1)] for px, py in poly[::2]],
            "nucleus": [[round(px, 1), round(py, 1)] for px, py in npoly[::2]],
            "abnormal": abnormal,
        })

    for _ in range(rng.randint(25, 70)):
        cx, cy = rng.uniform(0, W), rng.uniform(0, H)
        add_density(od, wavy_outline(cx, cy, rng.uniform(3, 5.5), rng, n=16, jitter=0.1), OD_HEMA,
                    rng.uniform(0.6, 1.0), 2, 0.3, 0.7, nprng)

    img = light * np.exp(-od)
    img += nprng.normal(0, 0.008, img.shape).astype(np.float32)  # sensor noise
    out = Image.fromarray(np.clip(img * 255, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.5))
    return out, cells


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    rng = random.Random(7)
    nprng = np.random.default_rng(7)
    meta = []
    for i in range(1, N_IMAGES + 1):
        img, cells = render(i, rng, nprng)
        name = f"fov_{i:02d}.jpg"
        img.save(OUT / name, quality=88)
        thumb = img.copy()
        thumb.thumbnail((320, 240))
        thumb.save(OUT / f"fov_{i:02d}_thumb.jpg", quality=82)
        meta.append({"file": name, "thumb": f"fov_{i:02d}_thumb.jpg", "width": W, "height": H, "cells": cells})
    (OUT / "samples.json").write_text(json.dumps(meta))
    print(f"wrote {len(meta)} images to {OUT}")


if __name__ == "__main__":
    main()
