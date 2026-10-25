/*
  Image viewer + annotation layer.

  OpenSeadragon handles zoom/pan/tiles. An SVG layer on top draws annotations in
  image-pixel coordinates (one transform keeps it in sync with the viewport).
  Handles, points, the crosshair and brush cursor are drawn in screen space so
  they stay the same size at every zoom level.
*/

import OpenSeadragon from "openseadragon";
import { forwardRef, memo, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import type { AnnotationVersion, Geometry, ID, Item, LabelClass } from "../types";
import { brushEdit, clampBox, closeRing, strokeToRegion, svgPath, toMulti, translate, type Pt } from "../lib/geometry";

export type Tool = "select" | "pan" | "box" | "polygon" | "point" | "brush";

export interface ViewerHandle {
  fit: () => void;
  zoomBy: (f: number) => void;
}

interface Props {
  item: Item;
  annotations: AnnotationVersion[];
  labels: Map<ID, LabelClass>;
  hiddenLabels: Set<ID>;
  showAnnotations: boolean;
  flagged: Set<ID>; // annotations failing a rule check
  selectedId: ID | null;
  tool: Tool;
  activeLabel: LabelClass | undefined;
  brushRadius: number;
  brushMode: "add" | "remove";
  readOnly?: boolean;
  onSelect: (id: ID | null) => void;
  onCreate: (shape: "bbox" | "polygon" | "point", g: Geometry) => void;
  onChangeGeometry: (id: ID, g: Geometry) => void;
  onBrushNew: (g: Geometry) => void;
  onCursor: (pt: Pt | null) => void;
  onScale: (s: number) => void;
}

type View = { x0: number; y0: number; s: number };

type Drag =
  | { type: "pan"; last: Pt }
  | { type: "move"; id: ID; start: Pt; orig: Geometry }
  | { type: "vertex"; id: ID; poly: number; v: number; orig: Geometry }
  | { type: "corner"; id: ID; corner: number; orig: Geometry & { kind: "bbox" } }
  | { type: "box"; start: Pt }
  | { type: "brush"; points: Pt[]; mode: "add" | "remove" };

export const UM_PER_PX = 0.25; // assumed 40x objective; real value comes from scan metadata later
const SCALE_STEPS = [2, 5, 10, 20, 50, 100, 200, 500, 1000];
const DRAW_TOOLS: Tool[] = ["box", "polygon", "point"];

export const Viewer = forwardRef<ViewerHandle, Props>(function Viewer(props, ref) {
  const {
    item, annotations, labels, hiddenLabels, showAnnotations, flagged, selectedId, tool,
    activeLabel, brushRadius, brushMode, readOnly, onSelect, onCreate, onChangeGeometry,
    onBrushNew, onCursor, onScale,
  } = props;

  const hostRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const osd = useRef<OpenSeadragon.Viewer | null>(null);
  const [view, setView] = useState<View>({ x0: 0, y0: 0, s: 1 });
  const viewRef = useRef(view);
  viewRef.current = view;

  const [cursor, setCursor] = useState<Pt | null>(null); // screen coords
  const [space, setSpace] = useState(false);
  const [preview, setPreview] = useState<{ id: ID; g: Geometry } | null>(null);
  const [draftBox, setDraftBox] = useState<Geometry | null>(null);
  const [draftPoly, setDraftPoly] = useState<Pt[]>([]);
  const [stroke, setStroke] = useState<{ points: Pt[]; mode: "add" | "remove" } | null>(null);
  const drag = useRef<Drag | null>(null);

  // ---- OpenSeadragon lifecycle ----
  useEffect(() => {
    if (!hostRef.current) return;
    const viewer = OpenSeadragon({
      element: hostRef.current,
      tileSources: sourceFor(item),
      showNavigationControl: false,
      showNavigator: true,
      navigatorPosition: "BOTTOM_RIGHT",
      navigatorSizeRatio: 0.16,
      navigatorAutoFade: false,
      animationTime: 0.35,
      springStiffness: 9,
      maxZoomPixelRatio: 6,
      minZoomImageRatio: 0.6,
      visibilityRatio: 0.6,
      gestureSettingsMouse: { clickToZoom: false, dblClickToZoom: false },
      crossOriginPolicy: "Anonymous",
    });
    osd.current = viewer;
    const update = () => {
      const vp = viewer.viewport;
      const p0 = vp.imageToViewerElementCoordinates(new OpenSeadragon.Point(0, 0));
      const p1 = vp.imageToViewerElementCoordinates(new OpenSeadragon.Point(1000, 0));
      const s = (p1.x - p0.x) / 1000;
      setView({ x0: p0.x, y0: p0.y, s });
    };
    viewer.addHandler("update-viewport", update);
    viewer.addHandler("open", update);
    viewer.addHandler("resize", update);
    return () => {
      viewer.destroy();
      osd.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const v = osd.current;
    if (!v) return;
    v.open(sourceFor(item));
    setDraftPoly([]);
    setDraftBox(null);
    setPreview(null);
  }, [item.url, item.dziUrl]);

  useEffect(() => onScale(view.s), [view.s, onScale]);

  useImperativeHandle(ref, () => ({
    fit: () => osd.current?.viewport.goHome(),
    zoomBy: (f: number) => {
      const vp = osd.current?.viewport;
      if (!vp) return;
      vp.zoomBy(f);
      vp.applyConstraints();
    },
  }));

  // Space held = temporary pan with any tool
  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.code === "Space" && !isTyping(e)) { setSpace(true); e.preventDefault(); }
      if (e.key === "Escape") { setDraftPoly([]); setDraftBox(null); drag.current = null; setStroke(null); }
      if (e.key === "Enter" && draftPolyRef.current.length >= 3) finishPolygon();
      if (e.key === "Backspace" && draftPolyRef.current.length > 0) { e.stopPropagation(); setDraftPoly((d) => d.slice(0, -1)); }
    };
    const up = (e: KeyboardEvent) => { if (e.code === "Space") setSpace(false); };
    window.addEventListener("keydown", down, true);
    window.addEventListener("keyup", up);
    return () => { window.removeEventListener("keydown", down, true); window.removeEventListener("keyup", up); };
  });

  const draftPolyRef = useRef(draftPoly);
  draftPolyRef.current = draftPoly;

  // Wheel zoom over the drawing layer (OSD only sees wheel events when the layer is click-through)
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    const onWheel = (e: WheelEvent) => {
      const v = osd.current;
      if (!v) return;
      e.preventDefault();
      const rect = svg.getBoundingClientRect();
      const pt = v.viewport.pointFromPixel(new OpenSeadragon.Point(e.clientX - rect.left, e.clientY - rect.top));
      v.viewport.zoomBy(e.deltaY < 0 ? 1.18 : 1 / 1.18, pt);
      v.viewport.applyConstraints();
    };
    svg.addEventListener("wheel", onWheel, { passive: false });
    return () => svg.removeEventListener("wheel", onWheel);
  }, []);

  const toImage = useCallback((clientX: number, clientY: number): { screen: Pt; img: Pt } => {
    const rect = svgRef.current!.getBoundingClientRect();
    const sx = clientX - rect.left, sy = clientY - rect.top;
    const { x0, y0, s } = viewRef.current;
    return { screen: [sx, sy], img: [(sx - x0) / s, (sy - y0) / s] };
  }, []);

  const insideImage = (p: Pt) => p[0] >= 0 && p[1] >= 0 && p[0] <= item.width && p[1] <= item.height;

  function finishPolygon() {
    // A double-click to close also lands as clicks: drop points within 3 screen px of the previous one.
    const minGap = 3 / viewRef.current.s;
    const pts = draftPolyRef.current.filter(
      (p, i, arr) => i === 0 || Math.hypot(p[0] - arr[i - 1][0], p[1] - arr[i - 1][1]) >= minGap,
    );
    if (pts.length >= 3) onCreate("polygon", { kind: "region", polygons: [[closeRing(pts)]] });
    setDraftPoly([]);
  }

  const selected = annotations.find((a) => a.annotationId === selectedId);

  // ---- pointer handling ----
  const onPointerDown = (e: React.PointerEvent) => {
    if (e.button === 1) { drag.current = { type: "pan", last: [e.clientX, e.clientY] }; capture(e); return; }
    if (e.button !== 0) return;
    const { img } = toImage(e.clientX, e.clientY);
    const target = (e.target as Element).closest("[data-id],[data-handle]") as HTMLElement | null;

    if (tool === "select") {
      const handle = target?.getAttribute("data-handle");
      if (handle && selected && !readOnly) {
        const [kind, a, b] = handle.split(":");
        if (kind === "v" && selected.geometry.kind === "region")
          drag.current = { type: "vertex", id: selected.annotationId, poly: +a, v: +b, orig: selected.geometry };
        if (kind === "c" && selected.geometry.kind === "bbox")
          drag.current = { type: "corner", id: selected.annotationId, corner: +a, orig: selected.geometry };
        capture(e);
        return;
      }
      const id = target?.getAttribute("data-id");
      if (id) {
        onSelect(id);
        const ann = annotations.find((x) => x.annotationId === id);
        if (ann && !readOnly) drag.current = { type: "move", id, start: img, orig: ann.geometry };
        capture(e);
        return;
      }
      onSelect(null);
      drag.current = { type: "pan", last: [e.clientX, e.clientY] };
      capture(e);
      return;
    }
    if (readOnly) return;
    if (tool === "box") { drag.current = { type: "box", start: img }; capture(e); return; }
    if (tool === "point") { if (insideImage(img)) onCreate("point", { kind: "point", x: round(img[0]), y: round(img[1]) }); return; }
    if (tool === "polygon") {
      if (!insideImage(img)) return;
      const first = draftPoly[0];
      if (first && draftPoly.length >= 3) {
        const { x0, y0, s } = viewRef.current;
        const d = Math.hypot(first[0] * s + x0 - (img[0] * s + x0), first[1] * s + y0 - (img[1] * s + y0));
        if (d < 9) { finishPolygon(); return; }
      }
      setDraftPoly((d) => [...d, img]);
      return;
    }
    if (tool === "brush") {
      const mode = e.altKey ? (brushMode === "add" ? "remove" : "add") : brushMode;
      drag.current = { type: "brush", points: [img], mode };
      setStroke({ points: [img], mode });
      capture(e);
    }
  };

  const onPointerMove = (e: React.PointerEvent) => {
    const { screen, img } = toImage(e.clientX, e.clientY);
    setCursor(screen);
    onCursor(insideImage(img) ? img : null);
    const d = drag.current;
    if (!d) return;
    if (d.type === "pan") {
      const v = osd.current!;
      const delta = v.viewport.deltaPointsFromPixels(new OpenSeadragon.Point(d.last[0] - e.clientX, d.last[1] - e.clientY));
      v.viewport.panBy(delta, true);
      d.last = [e.clientX, e.clientY];
    } else if (d.type === "move") {
      setPreview({ id: d.id, g: translate(d.orig, img[0] - d.start[0], img[1] - d.start[1]) });
    } else if (d.type === "vertex" && d.orig.kind === "region") {
      const polys = d.orig.polygons.map((p) => p.map((r) => r.map((q) => [q[0], q[1]] as Pt)));
      const ring = polys[d.poly][0];
      ring[d.v] = [round(img[0]), round(img[1])];
      if (d.v === 0) ring[ring.length - 1] = ring[0];
      setPreview({ id: d.id, g: { kind: "region", polygons: polys } });
    } else if (d.type === "corner") {
      const o = d.orig;
      const fixed: Pt = [d.corner === 0 || d.corner === 3 ? o.x + o.w : o.x, d.corner < 2 ? o.y + o.h : o.y];
      setPreview({ id: d.id, g: clampBox(fixed[0], fixed[1], img[0], img[1], item.width, item.height) });
    } else if (d.type === "box") {
      setDraftBox(clampBox(d.start[0], d.start[1], img[0], img[1], item.width, item.height));
    } else if (d.type === "brush") {
      const last = d.points.at(-1)!;
      if (Math.hypot(img[0] - last[0], img[1] - last[1]) > brushRadius * 0.25) {
        d.points.push(img);
        setStroke({ points: [...d.points], mode: d.mode });
      }
    }
  };

  const onPointerUp = () => {
    const d = drag.current;
    drag.current = null;
    if (!d) return;
    if ((d.type === "move" || d.type === "vertex" || d.type === "corner") && preview && preview.id === d.id) {
      if (JSON.stringify(preview.g) !== JSON.stringify(d.orig)) onChangeGeometry(d.id, preview.g);
      setPreview(null);
    } else if (d.type === "box") {
      if (draftBox && draftBox.kind === "bbox" && draftBox.w > 3 && draftBox.h > 3) onCreate("bbox", draftBox);
      setDraftBox(null);
    } else if (d.type === "brush") {
      const strokeRegion = strokeToRegion(d.points, brushRadius);
      const base = selected && selected.geometry.kind !== "point" && selected.labelId === activeLabel?.id ? toMulti(selected.geometry) : null;
      if (base) {
        const result = brushEdit(base, strokeRegion, d.mode);
        if (result.length === 0) onChangeGeometry(selected!.annotationId, { kind: "region", polygons: [] });
        else onChangeGeometry(selected!.annotationId, { kind: "region", polygons: result });
      } else if (d.mode === "add") {
        onBrushNew({ kind: "region", polygons: strokeRegion });
      }
      setStroke(null);
    }
  };

  const onDoubleClick = () => { if (tool === "polygon") finishPolygon(); };

  // ---- rendering ----
  const visible = useMemo(
    () => (showAnnotations ? annotations.filter((a) => !hiddenLabels.has(a.labelId)) : []),
    [annotations, hiddenLabels, showAnnotations],
  );
  const { x0, y0, s } = view;
  const passThrough = (tool === "pan" || space) && !drag.current;
  const drawing = DRAW_TOOLS.includes(tool) && !space;
  const selGeom = preview && selected && preview.id === selected.annotationId ? preview.g : selected?.geometry;

  return (
    <div className="viewer">
      <div ref={hostRef} className="viewer-osd" />
      <svg
        ref={svgRef}
        className={`viewer-layer tool-${tool}${space ? " space" : ""}`}
        style={{ pointerEvents: passThrough ? "none" : "auto" }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onPointerLeave={() => { setCursor(null); onCursor(null); }}
        onDoubleClick={onDoubleClick}
        onContextMenu={(e) => e.preventDefault()}
      >
        <g transform={`translate(${x0} ${y0}) scale(${s})`}>
          <Shapes anns={visible} labels={labels} selectedId={selectedId} flagged={flagged} previewId={preview?.id ?? null} />
          {preview && (
            <path d={svgPath(preview.g)} className="shape preview" fillRule="evenodd"
              style={{ ["--c" as string]: labels.get(selected?.labelId ?? "")?.color ?? "#fff" }} />
          )}
          {draftBox && <path d={svgPath(draftBox)} className="shape draft" style={{ ["--c" as string]: activeLabel?.color }} />}
          {stroke && (
            <polyline
              points={stroke.points.map((p) => p.join(",")).join(" ")}
              className={`brush-stroke ${stroke.mode}`}
              strokeWidth={brushRadius * 2}
              style={{ ["--c" as string]: activeLabel?.color }}
            />
          )}
        </g>

        {/* screen-space layer: points, handles, polygon draft, cursors */}
        {visible.filter((a) => a.geometry.kind === "point").map((a) => {
          const g = a.geometry as { kind: "point"; x: number; y: number };
          const c = labels.get(a.labelId)?.color;
          return (
            <g key={a.annotationId} data-id={a.annotationId} className={`pt${a.annotationId === selectedId ? " selected" : ""}`} style={{ ["--c" as string]: c }}>
              <circle cx={x0 + g.x * s} cy={y0 + g.y * s} r={a.annotationId === selectedId ? 7 : 5.5} />
            </g>
          );
        })}

        {tool === "select" && selGeom && !readOnly && <Handles g={selGeom} view={view} />}

        {draftPoly.length > 0 && (
          <g className="poly-draft" style={{ ["--c" as string]: activeLabel?.color }}>
            <polyline
              points={[...draftPoly, ...(cursor ? [[(cursor[0] - x0) / s, (cursor[1] - y0) / s] as Pt] : [])]
                .map(([x, y]) => `${x0 + x * s},${y0 + y * s}`).join(" ")}
            />
            {draftPoly.map(([x, y], i) => (
              <circle key={i} cx={x0 + x * s} cy={y0 + y * s} r={i === 0 ? 5 : 3} className={i === 0 ? "first" : ""} />
            ))}
          </g>
        )}

        {cursor && drawing && <Reticle at={cursor} color={activeLabel?.color} />}
        {cursor && tool === "brush" && !space && (
          <circle cx={cursor[0]} cy={cursor[1]} r={Math.max(2, brushRadius * s)} className={`brush-cursor ${brushMode}`} />
        )}
      </svg>
      <ScaleBar s={s} />
      {tool === "polygon" && draftPoly.length > 0 && (
        <div className="viewer-hint">
          {draftPoly.length} points. Click the first point, double-click or press <kbd>Enter</kbd> to close. <kbd>Esc</kbd> cancels.
        </div>
      )}
    </div>
  );
});

const Shapes = memo(function Shapes({
  anns, labels, selectedId, flagged, previewId,
}: { anns: AnnotationVersion[]; labels: Map<ID, LabelClass>; selectedId: ID | null; flagged: Set<ID>; previewId: ID | null }) {
  return (
    <>
      {anns.filter((a) => a.geometry.kind !== "point" && a.annotationId !== previewId).map((a) => (
        <path
          key={a.annotationId}
          data-id={a.annotationId}
          d={svgPath(a.geometry)}
          fillRule="evenodd"
          className={`shape${a.annotationId === selectedId ? " selected" : ""}${flagged.has(a.annotationId) ? " flagged" : ""}`}
          style={{ ["--c" as string]: labels.get(a.labelId)?.color ?? "#999" }}
        />
      ))}
    </>
  );
});

function Handles({ g, view }: { g: Geometry; view: View }) {
  const { x0, y0, s } = view;
  if (g.kind === "bbox") {
    const corners: Pt[] = [[g.x, g.y], [g.x + g.w, g.y], [g.x + g.w, g.y + g.h], [g.x, g.y + g.h]];
    return (
      <g className="handles">
        {corners.map(([x, y], i) => (
          <rect key={i} data-handle={`c:${i}`} x={x0 + x * s - 4} y={y0 + y * s - 4} width={8} height={8} />
        ))}
      </g>
    );
  }
  if (g.kind !== "region") return null;
  const total = g.polygons.reduce((t, p) => t + p[0].length, 0);
  if (total > 160) return null; // dense brushed outlines: edit with the brush instead
  return (
    <g className="handles">
      {g.polygons.map((poly, pi) =>
        poly[0].slice(0, -1).map(([x, y], vi) => (
          <circle key={`${pi}-${vi}`} data-handle={`v:${pi}:${vi}`} cx={x0 + x * s} cy={y0 + y * s} r={4} />
        )),
      )}
    </g>
  );
}

function Reticle({ at, color }: { at: Pt; color?: string }) {
  const [x, y] = at;
  const gap = 7;
  return (
    <g className="reticle" style={{ ["--c" as string]: color ?? "var(--hema)" }}>
      <line x1={0} y1={y} x2={x - gap} y2={y} />
      <line x1={x + gap} y1={y} x2="100%" y2={y} />
      <line x1={x} y1={0} x2={x} y2={y - gap} />
      <line x1={x} y1={y + gap} x2={x} y2="100%" />
      <circle cx={x} cy={y} r={2} />
    </g>
  );
}

function ScaleBar({ s }: { s: number }) {
  // pick the largest step that is under ~140 screen px
  const umPerScreenPx = UM_PER_PX / s;
  let um = SCALE_STEPS[0];
  for (const step of SCALE_STEPS) if (step / umPerScreenPx <= 140) um = step;
  const px = um / umPerScreenPx;
  return (
    <div className="scalebar" aria-label={`Scale bar ${um} micrometres`}>
      <div className="scalebar-bar" style={{ width: px }} />
      <span className="readout">{um} µm</span>
    </div>
  );
}

function capture(e: React.PointerEvent) {
  (e.currentTarget as Element).setPointerCapture?.(e.pointerId);
}
const round = (n: number) => Math.round(n * 10) / 10;
function isTyping(e: KeyboardEvent) {
  const t = e.target as HTMLElement;
  return t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable;
}

/** Large images come as Deep Zoom tiles; small ones as a single image. */
function sourceFor(item: Item): OpenSeadragon.TileSourceOptions | string {
  if (item.dziUrl) return item.dziUrl;
  return { type: "image", url: item.url, buildPyramid: true } as unknown as OpenSeadragon.TileSourceOptions;
}
