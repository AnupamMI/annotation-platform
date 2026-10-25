/* Geometry helpers. All coordinates are full-image pixels. */

import polygonClipping from "polygon-clipping";
import type { AnnotationVersion, Geometry, MultiPolygon, Ring } from "../types";

export type Pt = [number, number];

export function svgPath(g: Geometry): string {
  if (g.kind === "bbox") return `M${g.x} ${g.y}h${g.w}v${g.h}h${-g.w}z`;
  if (g.kind === "point") return "";
  return g.polygons
    .map((poly) => poly.map((ring) => "M" + ring.map((p) => `${p[0]} ${p[1]}`).join("L") + "Z").join(""))
    .join("");
}

export function bounds(g: Geometry): { x: number; y: number; w: number; h: number } {
  if (g.kind === "bbox") return { x: g.x, y: g.y, w: g.w, h: g.h };
  if (g.kind === "point") return { x: g.x, y: g.y, w: 0, h: 0 };
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const poly of g.polygons) for (const [x, y] of poly[0]) {
    if (x < x0) x0 = x;
    if (y < y0) y0 = y;
    if (x > x1) x1 = x;
    if (y > y1) y1 = y;
  }
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
}

function ringArea(r: Ring): number {
  let a = 0;
  for (let i = 0, j = r.length - 1; i < r.length; j = i++) a += (r[j][0] + r[i][0]) * (r[j][1] - r[i][1]);
  return Math.abs(a / 2);
}

export function area(g: Geometry): number {
  if (g.kind === "bbox") return g.w * g.h;
  if (g.kind === "point") return 0;
  return g.polygons.reduce((t, poly) => t + ringArea(poly[0]) - poly.slice(1).reduce((h, r) => h + ringArea(r), 0), 0);
}

export function vertexCount(g: Geometry): number {
  if (g.kind === "bbox") return 4;
  if (g.kind === "point") return 1;
  return g.polygons.reduce((t, poly) => t + poly.reduce((s, r) => s + Math.max(0, r.length - 1), 0), 0);
}

function pointInRing(pt: Pt, r: Ring): boolean {
  let inside = false;
  for (let i = 0, j = r.length - 1; i < r.length; j = i++) {
    const [xi, yi] = r[i], [xj, yj] = r[j];
    if (yi > pt[1] !== yj > pt[1] && pt[0] < ((xj - xi) * (pt[1] - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

export function contains(g: Geometry, pt: Pt): boolean {
  if (g.kind === "bbox") return pt[0] >= g.x && pt[0] <= g.x + g.w && pt[1] >= g.y && pt[1] <= g.y + g.h;
  if (g.kind === "point") return false;
  return g.polygons.some((poly) => pointInRing(pt, poly[0]) && !poly.slice(1).some((h) => pointInRing(pt, h)));
}

/** Every vertex of `inner` lies inside `outer` (good enough for the containment rule). */
export function within(inner: Geometry, outer: Geometry): boolean {
  if (inner.kind === "point") return contains(outer, [inner.x, inner.y]);
  if (inner.kind === "bbox") {
    const c: Pt[] = [[inner.x, inner.y], [inner.x + inner.w, inner.y], [inner.x, inner.y + inner.h], [inner.x + inner.w, inner.y + inner.h]];
    return c.every((p) => contains(outer, p));
  }
  return inner.polygons.every((poly) => poly[0].every((p) => contains(outer, p)));
}

export function translate(g: Geometry, dx: number, dy: number): Geometry {
  if (g.kind === "bbox") return { ...g, x: g.x + dx, y: g.y + dy };
  if (g.kind === "point") return { ...g, x: g.x + dx, y: g.y + dy };
  return { kind: "region", polygons: g.polygons.map((poly) => poly.map((r) => r.map(([x, y]) => [x + dx, y + dy] as Pt))) };
}

export function clampBox(x0: number, y0: number, x1: number, y1: number, W: number, H: number): Geometry {
  const ax = Math.max(0, Math.min(W, Math.min(x0, x1)));
  const ay = Math.max(0, Math.min(H, Math.min(y0, y1)));
  const bx = Math.max(0, Math.min(W, Math.max(x0, x1)));
  const by = Math.max(0, Math.min(H, Math.max(y0, y1)));
  return { kind: "bbox", x: round1(ax), y: round1(ay), w: round1(bx - ax), h: round1(by - ay) };
}

export const round1 = (n: number) => Math.round(n * 10) / 10;

export function closeRing(pts: Pt[]): Ring {
  const r = pts.map(([x, y]) => [round1(x), round1(y)] as Pt);
  if (r.length && (r[0][0] !== r.at(-1)![0] || r[0][1] !== r.at(-1)![1])) r.push([r[0][0], r[0][1]]);
  return r;
}

function circle(cx: number, cy: number, rad: number, n = 20): Ring {
  const r: Ring = [];
  for (let i = 0; i < n; i++) {
    const a = (2 * Math.PI * i) / n;
    r.push([cx + rad * Math.cos(a), cy + rad * Math.sin(a)]);
  }
  r.push([r[0][0], r[0][1]]);
  return r;
}

/** Turns a brush stroke (pointer samples) into a filled region. */
export function strokeToRegion(points: Pt[], radius: number): MultiPolygon {
  const stamps: Pt[] = [];
  for (let i = 0; i < points.length; i++) {
    if (i === 0) { stamps.push(points[0]); continue; }
    const [ax, ay] = points[i - 1], [bx, by] = points[i];
    const d = Math.hypot(bx - ax, by - ay);
    const steps = Math.max(1, Math.ceil(d / (radius * 0.45)));
    for (let k = 1; k <= steps; k++) stamps.push([ax + ((bx - ax) * k) / steps, ay + ((by - ay) * k) / steps]);
  }
  const circles = stamps.map(([x, y]) => [circle(x, y, radius)] as Ring[]);
  if (circles.length === 1) return [circles[0]];
  return roundMulti(polygonClipping.union(circles[0], ...circles.slice(1)) as MultiPolygon);
}

/** The flagship ADD / REMOVE edit: union or difference with a brush stroke. */
export function brushEdit(base: MultiPolygon | null, stroke: MultiPolygon, mode: "add" | "remove"): MultiPolygon {
  if (!base || base.length === 0) return mode === "add" ? stroke : [];
  const out = mode === "add" ? polygonClipping.union(base, stroke) : polygonClipping.difference(base, stroke);
  return simplifyMulti(roundMulti(out as MultiPolygon), 0.6);
}

function roundMulti(m: MultiPolygon): MultiPolygon {
  return m.map((poly) => poly.map((r) => r.map(([x, y]) => [round1(x), round1(y)] as Pt)));
}

/** Drops vertices closer than `tol` pixels to keep brushed outlines light. */
function simplifyMulti(m: MultiPolygon, tol: number): MultiPolygon {
  return m.map((poly) =>
    poly.map((r) => {
      const out: Ring = [r[0]];
      for (let i = 1; i < r.length - 1; i++) {
        const p = out.at(-1)!;
        if (Math.hypot(r[i][0] - p[0], r[i][1] - p[1]) >= tol) out.push(r[i]);
      }
      out.push(r.at(-1)!);
      return out;
    }),
  );
}

export function toMulti(g: Geometry): MultiPolygon | null {
  if (g.kind === "region") return g.polygons;
  if (g.kind === "bbox") return [[[[g.x, g.y], [g.x + g.w, g.y], [g.x + g.w, g.y + g.h], [g.x, g.y + g.h], [g.x, g.y]]]];
  return null;
}

/** Rule check: labels with mustBeInsideId whose shape is not inside any annotation of the parent label. */
export function containmentProblems(
  anns: AnnotationVersion[],
  rules: Map<string, string>, // labelId -> must-be-inside labelId
): AnnotationVersion[] {
  const out: AnnotationVersion[] = [];
  for (const a of anns) {
    const parent = rules.get(a.labelId);
    if (!parent) continue;
    const ok = anns.some((p) => p.labelId === parent && p.annotationId !== a.annotationId && within(a.geometry, p.geometry));
    if (!ok) out.push(a);
  }
  return out;
}
