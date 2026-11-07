"""
Makes public/og.png, the 1200 x 630 link preview: a sample field of view with its cell
outlines, beside the product name. Run from frontend/:  python scripts/make_og.py
"""

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent.parent
NAME = "Annotation ExE"
LINES = ["People label.", "AI handles the data."]
BG, INK, DIM, GREEN, HEMA, ORANGE = "#14181e", "#dde3ea", "#9aa5b3", "#3fb59a", "#7b80e0", "#e9a13b"


def font(size: int, bold: bool = False):
    names = (["IBMPlexSans-SemiBold.ttf", "segoeuib.ttf", "DejaVuSans-Bold.ttf"] if bold
             else ["IBMPlexSans-Regular.ttf", "segoeui.ttf", "DejaVuSans.ttf"])
    for n in names:
        try:
            return ImageFont.truetype(n, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def pts(poly, scale, dx, dy):
    out = []
    for p in poly:
        x, y = (p.split() if isinstance(p, str) else p)
        out.append((float(x) * scale + dx, float(y) * scale + dy))
    return out


def main():
    W, H = 1200, 630
    img = Image.new("RGB", (W, H), BG)
    sample = next(s for s in json.loads((HERE / "public/samples/samples.json").read_text()) if s["file"] == "fov_03.jpg")
    fov = Image.open(HERE / "public/samples" / sample["file"]).convert("RGB")
    # right half: the field of view, cropped to fill
    box_w, box_h = 560, H
    scale = max(box_w / fov.width, box_h / fov.height)
    fov_s = fov.resize((round(fov.width * scale), round(fov.height * scale)), Image.LANCZOS)
    ox = fov_s.width - box_w  # keep the right edge, where the abnormal cell is
    oy = (fov_s.height - box_h) // 2
    view = fov_s.crop((ox, oy, ox + box_w, oy + box_h))
    d = ImageDraw.Draw(view)  # drawn on the crop, so outlines stop at its edge
    for c in sample["cells"]:
        color = ORANGE if c.get("abnormal") else GREEN
        d.polygon(pts(c["cell"], scale, -ox, -oy), outline=color, width=3)
        d.polygon(pts(c["nucleus"], scale, -ox, -oy), outline=HEMA, width=2)
    img.paste(view, (W - box_w, 0))

    d = ImageDraw.Draw(img)
    # the mark: a reticle over a slide
    mx, my, u = 72, 92, 2.6
    d.rounded_rectangle((mx + 2 * u, my + 7 * u, mx + 24 * u, my + 19 * u), radius=4, outline="#6b7686", width=3)
    d.ellipse((mx + 7.8 * u, my + 7.8 * u, mx + 18.2 * u, my + 18.2 * u), outline=GREEN, width=4)
    for a, b in [((13, 4.5), (13, 8.5)), ((13, 17.5), (13, 21.5)), ((4.5, 13), (8.5, 13)), ((17.5, 13), (21.5, 13))]:
        d.line((mx + a[0] * u, my + a[1] * u, mx + b[0] * u, my + b[1] * u), fill=GREEN, width=4)
    d.text((mx + 82, my + 18), NAME, font=font(34, bold=True), fill=INK)
    y = 250
    for line in LINES:
        d.text((72, y), line, font=font(56, bold=True), fill=INK)
        y += 70
    d.text((72, y + 26), "Image annotation for microscopy,", font=font(26), fill=DIM)
    d.text((72, y + 62), "whole-slide images and beyond.", font=font(26), fill=DIM)
    img.save(HERE / "public/og.png", optimize=True)
    print("wrote public/og.png")


if __name__ == "__main__":
    main()
