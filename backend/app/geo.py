"""
Conversion between the API's geometry JSON and PostGIS (via Shapely).

API shapes (full-image pixel coordinates):
  {"kind": "bbox",   "x", "y", "w", "h"}
  {"kind": "region", "polygons": [[outer_ring, hole, ...], ...]}   # MultiPolygon
  {"kind": "point",  "x", "y"}

Boxes are stored as polygons; the annotation's shape_type remembers it was a box.
"""

from __future__ import annotations

from geoalchemy2.shape import from_shape, to_shape
from shapely import make_valid
from shapely.geometry import MultiPolygon, Point, Polygon, box
from shapely.geometry.base import BaseGeometry

from app.models.annotation import ShapeType


class GeometryError(ValueError):
    pass


def _r(v: float) -> float:
    return round(float(v), 1)


def to_shapely(g: dict) -> BaseGeometry:
    kind = g.get("kind")
    if kind == "bbox":
        if g["w"] <= 0 or g["h"] <= 0:
            raise GeometryError("A box needs a positive width and height.")
        return box(g["x"], g["y"], g["x"] + g["w"], g["y"] + g["h"])
    if kind == "point":
        return Point(g["x"], g["y"])
    if kind == "region":
        polys = []
        for poly in g.get("polygons", []):
            if not poly or len(poly[0]) < 4:
                raise GeometryError("Each polygon needs at least three points.")
            polys.append(Polygon(poly[0], poly[1:]))
        if not polys:
            raise GeometryError("A region needs at least one polygon.")
        geom: BaseGeometry = MultiPolygon(polys)
        if not geom.is_valid:
            # e.g. a self-crossing outline: repair it rather than reject the annotator's work
            fixed = make_valid(geom)
            parts = [p for p in getattr(fixed, "geoms", [fixed]) if isinstance(p, Polygon)]
            if not parts:
                raise GeometryError("The outline could not be repaired into a valid polygon.")
            geom = MultiPolygon(parts)
        if geom.area <= 0:
            raise GeometryError("The outline has zero area.")
        return geom
    raise GeometryError(f"Unknown geometry kind: {kind!r}")


def to_db(g: dict):
    return from_shape(to_shapely(g), srid=0)


def from_db(value, shape: ShapeType) -> dict | None:
    if value is None:
        return None
    return from_shapely(to_shape(value), shape)


def from_shapely(geom: BaseGeometry, shape: ShapeType | str) -> dict:
    shape = ShapeType(shape)
    if shape == ShapeType.point:
        return {"kind": "point", "x": _r(geom.x), "y": _r(geom.y)}
    if shape == ShapeType.bbox:
        x0, y0, x1, y1 = geom.bounds
        return {"kind": "bbox", "x": _r(x0), "y": _r(y0), "w": _r(x1 - x0), "h": _r(y1 - y0)}
    polys = list(geom.geoms) if hasattr(geom, "geoms") else [geom]
    polys = [p for p in polys if isinstance(p, Polygon) and not p.is_empty]
    return {
        "kind": "region",
        "polygons": [
            [[[_r(x), _r(y)] for x, y in p.exterior.coords]]
            + [[[_r(x), _r(y)] for x, y in hole.coords] for hole in p.interiors]
            for p in polys
        ],
    }
