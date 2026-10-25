import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { currentAnnotations, historyOf, latestVersion, useStore } from "../store";
import { formatClock, relativeTime, STATUS_LABEL, useCan, userName } from "../hooks";
import { Viewer, UM_PER_PX, type Tool, type ViewerHandle } from "../components/Viewer";
import { Icon } from "../components/Icon";
import { area, containmentProblems, vertexCount, type Pt } from "../lib/geometry";
import { useActiveTime } from "../lib/useActiveTime";
import type { AnnotationVersion, Geometry, ID, LabelClass, ShapeType } from "../types";

const TOOLS: { id: Tool; label: string; key: string; icon: string; shape?: ShapeType }[] = [
  { id: "select", label: "Select and edit", key: "V", icon: "select" },
  { id: "pan", label: "Pan", key: "H", icon: "pan" },
  { id: "box", label: "Box", key: "B", icon: "box", shape: "bbox" },
  { id: "polygon", label: "Polygon", key: "P", icon: "polygon", shape: "polygon" },
  { id: "brush", label: "Brush: add or remove area", key: "R", icon: "brush", shape: "polygon" },
  { id: "point", label: "Point", key: "D", icon: "point", shape: "point" },
];

type UndoEntry = { annotationId: ID; before: AnnotationVersion | null; after: AnnotationVersion | null };

export function WorkspacePage() {
  const { projectId, itemId } = useParams();
  const [search] = useSearchParams();
  const can = useCan();
  const reviewMode = search.get("mode") === "review" && can.review;
  // Read-only: reviewing (reviewers judge, they don't redraw here), or a role that can't annotate
  const locked = reviewMode || !can.annotate;
  const navigate = useNavigate();
  const store = useStore();
  const { items, labels: allLabels, annotations, users } = store;

  const item = items.find((i) => i.id === itemId);
  const projectItems = useMemo(() => items.filter((i) => i.projectId === projectId), [items, projectId]);
  const labels = useMemo(() => allLabels.filter((l) => l.projectId === projectId), [allLabels, projectId]);
  const labelMap = useMemo(() => new Map(labels.map((l) => [l.id, l])), [labels]);
  const anns = useMemo(() => (item ? currentAnnotations(annotations, item.id) : []), [annotations, item]);

  const [tool, setTool] = useState<Tool>("select");
  const [activeLabelId, setActiveLabelId] = useState<ID>(labels[0]?.id ?? "");
  const [selectedId, setSelectedId] = useState<ID | null>(null);
  const [hidden, setHidden] = useState<Set<ID>>(new Set());
  const [showAnns, setShowAnns] = useState(true);
  const [brushRadius, setBrushRadius] = useState(10);
  const [brushMode, setBrushMode] = useState<"add" | "remove">("add");
  const [cursor, setCursor] = useState<Pt | null>(null);
  const [scale, setScale] = useState(1);
  const [help, setHelp] = useState(false);
  const [reviewNote, setReviewNote] = useState("");
  const viewer = useRef<ViewerHandle>(null);
  const undo = useRef<UndoEntry[]>([]);
  const redo = useRef<UndoEntry[]>([]);
  const [, bump] = useState(0);

  const activeLabel = labelMap.get(activeLabelId);
  const selected = anns.find((a) => a.annotationId === selectedId) ?? null;

  const rules = useMemo(() => new Map(labels.filter((l) => l.mustBeInsideId).map((l) => [l.id, l.mustBeInsideId!])), [labels]);
  const problems = useMemo(() => containmentProblems(anns, rules), [anns, rules]);
  const flagged = useMemo(() => new Set(problems.map((p) => p.annotationId)), [problems]);

  const { ms: activeMs, idle } = useActiveTime(item?.id ?? "none", (ms, key) => {
    if (key !== "none" && ms >= 5000) useStore.getState().logEvent({ projectId: projectId!, itemId: key, action: "item.worked", activeMs: ms });
  });

  useEffect(() => {
    setSelectedId(null);
    undo.current = [];
    redo.current = [];
    bump((n) => n + 1);
  }, [itemId]);

  // ---- editing actions (each one appends versions; nothing is overwritten) ----
  const markStarted = useCallback(() => {
    if (!item) return;
    if (item.status === "new" || item.status === "rejected") store.setItemStatus(item.id, "in_progress");
    if (item.status === "done" || item.status === "in_review" || item.status === "annotated") {
      store.setItemStatus(item.id, "in_progress");
      store.toast(`${item.name} was reopened because it changed after submission. Submit it again when you're done.`, "warn");
    }
  }, [item, store]);

  const record = (annotationId: ID, before: AnnotationVersion | null, after: AnnotationVersion | null) => {
    undo.current.push({ annotationId, before, after });
    redo.current = [];
    bump((n) => n + 1);
    markStarted();
  };

  const shapeAllowed = (label: LabelClass | undefined, shape: ShapeType) => {
    if (!label) { store.toast("Choose a label first.", "warn"); return false; }
    if (!label.allowedShapes.includes(shape)) {
      store.toast(`${label.name} can only be drawn as ${shapeNames(label.allowedShapes)}.`, "warn");
      return false;
    }
    return true;
  };

  const handleCreate = (shape: "bbox" | "polygon" | "point", g: Geometry) => {
    if (!item || !shapeAllowed(activeLabel, shape)) return;
    const v = store.createAnnotation(item.id, activeLabel!.id, shape, g);
    record(v.annotationId, null, v);
    setSelectedId(v.annotationId);
  };

  const handleBrushNew = (g: Geometry) => {
    if (!item || !shapeAllowed(activeLabel, "polygon")) return;
    const v = store.createAnnotation(item.id, activeLabel!.id, "polygon", g);
    record(v.annotationId, null, v);
    setSelectedId(v.annotationId);
  };

  const handleGeometry = (id: ID, g: Geometry) => {
    const before = latestVersion(store.annotations, id) ?? null;
    if (g.kind === "region" && g.polygons.length === 0) {
      const v = store.deleteAnnotation(id);
      if (v) record(id, before, null);
      setSelectedId(null);
      return;
    }
    const v = store.updateAnnotation(id, { geometry: g });
    if (v) record(id, before, v);
  };

  const relabel = (id: ID, label: LabelClass) => {
    const cur = anns.find((a) => a.annotationId === id);
    if (!cur || cur.labelId === label.id) return;
    if (!label.allowedShapes.includes(cur.shape)) {
      store.toast(`${label.name} can only be drawn as ${shapeNames(label.allowedShapes)}.`, "warn");
      return;
    }
    const before = latestVersion(store.annotations, id) ?? null;
    const v = store.updateAnnotation(id, { labelId: label.id });
    if (v) record(id, before, v);
  };

  const remove = (id: ID) => {
    const before = latestVersion(store.annotations, id) ?? null;
    if (store.deleteAnnotation(id)) record(id, before, null);
    setSelectedId(null);
  };

  const applyState = (annotationId: ID, target: AnnotationVersion | null) => {
    if (target === null) store.deleteAnnotation(annotationId);
    else store.restoreAnnotation(annotationId, target);
  };
  const doUndo = () => {
    const e = undo.current.pop();
    if (!e) return;
    applyState(e.annotationId, e.before);
    redo.current.push(e);
    bump((n) => n + 1);
  };
  const doRedo = () => {
    const e = redo.current.pop();
    if (!e) return;
    applyState(e.annotationId, e.after);
    undo.current.push(e);
    bump((n) => n + 1);
  };

  // ---- navigation ----
  const idx = projectItems.findIndex((i) => i.id === itemId);
  const go = (id: ID | undefined) => id && navigate(`/p/${projectId}/annotate/${id}${reviewMode ? "?mode=review" : ""}`);
  const nextOpen = () => {
    const order = [...projectItems.slice(idx + 1), ...projectItems.slice(0, idx)];
    const target = reviewMode
      ? order.find((i) => i.status === "in_review")
      : order.find((i) => ["new", "in_progress", "rejected"].includes(i.status));
    if (target) go(target.id);
    else store.toast(reviewMode ? "No other images are waiting for review." : "Every other image is annotated.", "ok");
  };

  const submit = () => {
    if (!item) return;
    if (anns.length === 0) { store.toast("Add at least one annotation before submitting.", "warn"); return; }
    if (problems.length) store.toast(`Submitted with ${problems.length} rule warning${problems.length > 1 ? "s" : ""}. The reviewer will see them.`, "warn");
    store.setItemStatus(item.id, "in_review");
    store.logEvent({ projectId: projectId!, itemId: item.id, action: "item.submitted", activeMs });
    if (!problems.length) store.toast(`${item.name} submitted for review.`);
    nextOpen();
  };

  const decide = (verdict: "approved" | "rejected") => {
    if (!item) return;
    if (verdict === "rejected" && !reviewNote.trim()) { store.toast("Add a note so the annotator knows what to change.", "warn"); return; }
    store.addReview({ itemId: item.id, verdict, comment: reviewNote.trim() });
    store.setItemStatus(item.id, verdict === "approved" ? "done" : "rejected");
    store.logEvent({ projectId: projectId!, itemId: item.id, action: `review.${verdict}`, activeMs });
    store.toast(verdict === "approved" ? `${item.name} approved.` : `Changes requested on ${item.name}.`, verdict === "approved" ? "ok" : "warn");
    setReviewNote("");
    nextOpen();
  };

  // ---- keyboard ----
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT") return;
      const k = e.key.toLowerCase();
      if ((e.ctrlKey || e.metaKey) && k === "z") { e.preventDefault(); e.shiftKey ? doRedo() : doUndo(); return; }
      if ((e.ctrlKey || e.metaKey) && k === "y") { e.preventDefault(); doRedo(); return; }
      if ((e.ctrlKey || e.metaKey) && k === "enter") { e.preventDefault(); if (reviewMode) decide("approved"); else if (!locked) submit(); return; }
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      const toolHit = TOOLS.find((x) => x.key.toLowerCase() === k);
      if (toolHit && (!locked || toolHit.id === "select" || toolHit.id === "pan")) { setTool(toolHit.id); return; }
      const labelHit = labels.find((l) => l.hotkey === e.key);
      if (labelHit) {
        setActiveLabelId(labelHit.id);
        // Only relabel when editing in Select mode; while drawing, number keys pick the next label.
        if (selectedId && !locked && tool === "select") relabel(selectedId, labelHit);
        return;
      }
      if ((k === "delete" || k === "backspace") && selectedId && !locked) { e.preventDefault(); remove(selectedId); return; }
      if (k === "escape") setSelectedId(null);
      if (k === "f") viewer.current?.fit();
      if (k === "a") setShowAnns((v) => !v);
      if (k === "]") go(projectItems[idx + 1]?.id);
      if (k === "[") go(projectItems[idx - 1]?.id);
      if (k === "n" && e.shiftKey) nextOpen();
      if (k === "-") setBrushRadius((r) => Math.max(2, r - 2));
      if (k === "=" || k === "+") setBrushRadius((r) => Math.min(80, r + 2));
      if (k === "x" && tool === "brush") setBrushMode((m) => (m === "add" ? "remove" : "add"));
      if (e.key === "?") setHelp((h) => !h);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  if (!item) {
    return (
      <div className="empty">
        <h2>Image not found</h2>
        <Link className="btn" to={`/p/${projectId}/data`}>Back to data</Link>
      </div>
    );
  }

  const countByLabel = (id: ID) => anns.filter((a) => a.labelId === id).length;
  const latestReview = store.reviews.filter((r) => r.itemId === item.id).sort((a, b) => b.at.localeCompare(a.at))[0];

  return (
    <div className={`workspace${reviewMode ? " review-mode" : ""}`}>
      {/* item bar */}
      <div className="ws-itembar">
        <div className="ws-item-id">
          <span className="readout ws-item-name">{item.name}</span>
          <span className={`tag ${item.status}`}>{STATUS_LABEL[item.status]}</span>
          <span className="dim">{item.assigneeId ? `Assigned to ${userName(item.assigneeId)}` : "Not assigned"}</span>
        </div>
        <div className="ws-nav">
          <button className="btn sm icon ghost" onClick={() => go(projectItems[idx - 1]?.id)} disabled={idx <= 0} aria-label="Previous image" title="Previous image  [">
            <Icon name="prev" size={16} />
          </button>
          <span className="readout dim">{idx + 1} / {projectItems.length}</span>
          <button className="btn sm icon ghost" onClick={() => go(projectItems[idx + 1]?.id)} disabled={idx >= projectItems.length - 1} aria-label="Next image" title="Next image  ]">
            <Icon name="next" size={16} />
          </button>
          <button className="btn sm" onClick={nextOpen} title="Shift N">
            {reviewMode ? "Next to review" : "Next unannotated"}
          </button>
        </div>
        <div className="ws-actions">
          {reviewMode ? (
            <>
              <input className="input review-note" placeholder="Note for the annotator" value={reviewNote} onChange={(e) => setReviewNote(e.target.value)} />
              <button className="btn danger" onClick={() => decide("rejected")}>Request changes</button>
              <button className="btn primary" onClick={() => decide("approved")}>
                <Icon name="check" size={15} /> Approve
              </button>
            </>
          ) : !can.annotate ? (
            <span className="dim">View only{can.role ? ` (${can.role})` : ""}</span>
          ) : (
            <>
              {can.review && item.status === "in_review" && (
                <button className="btn" onClick={() => navigate(`/p/${projectId}/annotate/${item.id}?mode=review`)}>Review this image</button>
              )}
              <button className="btn primary" onClick={submit} disabled={item.status === "in_review" || item.status === "done"} title="Ctrl Enter">
                {item.status === "in_review" ? "Waiting for review" : item.status === "done" ? "Approved" : "Submit for review"}
              </button>
            </>
          )}
        </div>
      </div>

      {item.status === "rejected" && latestReview?.verdict === "rejected" && !reviewMode && (
        <div className="ws-banner">
          <Icon name="alert" size={16} />
          <span><strong>{userName(latestReview.reviewerId)} requested changes:</strong> {latestReview.comment}</span>
        </div>
      )}

      {/* tools */}
      <div className="ws-tools" role="toolbar" aria-label="Tools">
        {TOOLS.map((t) => (
          <button
            key={t.id}
            className={`tool${tool === t.id ? " active" : ""}`}
            onClick={() => setTool(t.id)}
            disabled={locked && t.id !== "select" && t.id !== "pan"}
            title={`${t.label}  (${t.key})`}
            aria-pressed={tool === t.id}
          >
            <Icon name={t.icon} size={19} />
            <span className="tool-key">{t.key}</span>
          </button>
        ))}
        <div className="tool-sep" />
        <button className="tool" onClick={doUndo} disabled={!undo.current.length} title="Undo  (Ctrl Z)"><Icon name="undo" size={19} /></button>
        <button className="tool" onClick={doRedo} disabled={!redo.current.length} title="Redo  (Ctrl Shift Z)"><Icon name="redo" size={19} /></button>
        <div className="tool-sep" />
        <button className="tool" onClick={() => viewer.current?.fit()} title="Fit image  (F)"><Icon name="fit" size={19} /></button>
        <button className="tool" onClick={() => setShowAnns((v) => !v)} title="Show or hide annotations  (A)" aria-pressed={!showAnns}>
          <Icon name={showAnns ? "eye" : "eyeOff"} size={19} />
        </button>
        <div className="tool-spacer" />
        <button className="tool" onClick={() => setHelp(true)} title="Keyboard shortcuts  (?)"><Icon name="keyboard" size={19} /></button>
      </div>

      {/* viewer */}
      <div className="ws-stage">
        <Viewer
          ref={viewer}
          item={item}
          annotations={anns}
          labels={labelMap}
          hiddenLabels={hidden}
          showAnnotations={showAnns}
          flagged={flagged}
          selectedId={selectedId}
          tool={tool}
          activeLabel={activeLabel}
          brushRadius={brushRadius}
          brushMode={brushMode}
          readOnly={locked}
          onSelect={setSelectedId}
          onCreate={handleCreate}
          onChangeGeometry={handleGeometry}
          onBrushNew={handleBrushNew}
          onCursor={setCursor}
          onScale={setScale}
        />
        {tool === "brush" && (
          <div className="brush-panel">
            <div className="seg" role="group" aria-label="Brush mode">
              <button className={brushMode === "add" ? "on" : ""} onClick={() => setBrushMode("add")}>Add</button>
              <button className={brushMode === "remove" ? "on" : ""} onClick={() => setBrushMode("remove")}>Remove</button>
            </div>
            <label className="brush-size">
              <span className="dim">Size</span>
              <input type="range" min={2} max={80} value={brushRadius} onChange={(e) => setBrushRadius(+e.target.value)} />
              <span className="readout">{brushRadius * 2}px</span>
            </label>
            <span className="faint">Hold <kbd>Alt</kbd> to switch mode while painting</span>
          </div>
        )}
      </div>

      {/* inspector */}
      <aside className="ws-inspector" aria-label="Inspector">
        <section className="insp-section">
          <div className="insp-head"><h4>Labels</h4><span className="faint">Number keys</span></div>
          <ul className="label-list">
            {labels.map((l) => (
              <li key={l.id} className={l.id === activeLabelId ? "active" : ""}>
                <button className="label-pick" onClick={() => { setActiveLabelId(l.id); if (selectedId && !locked && tool === "select") relabel(selectedId, l); }}>
                  <kbd>{l.hotkey}</kbd>
                  <span className="swatch" style={{ background: l.color }} />
                  <span className="label-name">{l.name}</span>
                  <span className="readout faint">{countByLabel(l.id)}</span>
                </button>
                <button
                  className="btn ghost sm icon"
                  aria-label={hidden.has(l.id) ? `Show ${l.name}` : `Hide ${l.name}`}
                  onClick={() => setHidden((h) => { const n = new Set(h); n.has(l.id) ? n.delete(l.id) : n.add(l.id); return n; })}
                >
                  <Icon name={hidden.has(l.id) ? "eyeOff" : "eye"} size={14} />
                </button>
              </li>
            ))}
          </ul>
        </section>

        {problems.length > 0 && (
          <section className="insp-section">
            <div className="insp-head"><h4>Rule checks</h4><span className="readout warn-count">{problems.length}</span></div>
            <ul className="problem-list">
              {problems.slice(0, 6).map((p) => (
                <li key={p.annotationId}>
                  <button onClick={() => { setSelectedId(p.annotationId); setTool("select"); }}>
                    <Icon name="alert" size={14} />
                    {labelMap.get(p.labelId)?.name} is not inside a {labelMap.get(rules.get(p.labelId)!)?.name.toLowerCase()}
                  </button>
                </li>
              ))}
            </ul>
          </section>
        )}

        {selected ? (
          <SelectedPanel
            ann={selected}
            label={labelMap.get(selected.labelId)}
            history={historyOf(store.annotations, selected.annotationId)}
            readOnly={locked}
            onDelete={() => remove(selected.annotationId)}
            onRestore={(v) => {
              const before = latestVersion(store.annotations, selected.annotationId) ?? null;
              const after = store.restoreAnnotation(selected.annotationId, v);
              record(selected.annotationId, before, after);
            }}
          />
        ) : (
          <section className="insp-section">
            <div className="insp-head"><h4>Annotations</h4><span className="readout faint">{anns.length}</span></div>
            {anns.length === 0 ? (
              <p className="dim insp-empty">
                Nothing on this image yet. Pick a label, then draw with <kbd>B</kbd> box, <kbd>P</kbd> polygon or <kbd>R</kbd> brush.
              </p>
            ) : (
              <ul className="ann-list">
                {anns.slice().reverse().map((a) => (
                  <li key={a.annotationId}>
                    <button onClick={() => setSelectedId(a.annotationId)} className={flagged.has(a.annotationId) ? "flag" : ""}>
                      <span className="swatch" style={{ background: labelMap.get(a.labelId)?.color }} />
                      <span>{labelMap.get(a.labelId)?.name}</span>
                      <span className="readout faint">v{a.version}</span>
                      <span className="faint ann-src">{a.source === "import" ? "Imported" : initialsOf(users, a.createdBy)}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>
        )}
      </aside>

      {/* instrument status strip */}
      <footer className="ws-status">
        <span className="readout stat-coord">
          {cursor ? <>x {fmt(cursor[0])}  y {fmt(cursor[1])} px</> : <span className="faint">x –  y –</span>}
        </span>
        {cursor && <span className="readout dim">{fmt(cursor[0] * UM_PER_PX)}, {fmt(cursor[1] * UM_PER_PX)} µm</span>}
        <span className="readout">{scale >= 1 ? scale.toFixed(1) : scale.toFixed(2)}×</span>
        <span className="dim">{item.width} × {item.height}</span>
        <span className="stat-spacer" />
        <span className="dim">{TOOLS.find((t) => t.id === tool)?.label}</span>
        {activeLabel && (
          <span className="stat-label"><span className="swatch" style={{ background: activeLabel.color }} />{activeLabel.name}</span>
        )}
        <span className={`stat-time${idle ? " idle" : ""}`} title="Active time on this image. Pauses after 60 seconds without input.">
          <span className="pulse" />
          <span className="readout">{formatClock(activeMs)}</span>
          <span className="dim">{idle ? "paused" : "active"}</span>
        </span>
        <SaveNote />
      </footer>

      {help && <ShortcutsDialog onClose={() => setHelp(false)} />}
    </div>
  );
}

function SelectedPanel({
  ann, label, history, readOnly, onDelete, onRestore,
}: {
  ann: AnnotationVersion;
  label: LabelClass | undefined;
  history: AnnotationVersion[];
  readOnly: boolean;
  onDelete: () => void;
  onRestore: (v: AnnotationVersion) => void;
}) {
  const users = useStore((s) => s.users);
  const px = area(ann.geometry);
  return (
    <section className="insp-section">
      <div className="insp-head">
        <h4 className="sel-title"><span className="swatch" style={{ background: label?.color }} />{label?.name}</h4>
        {!readOnly && (
          <button className="btn ghost sm" onClick={onDelete} title="Delete  (Del)">
            <Icon name="trash" size={14} /> Delete
          </button>
        )}
      </div>
      <dl className="facts">
        <dt>Shape</dt><dd>{ann.shape === "bbox" ? "Box" : ann.shape === "polygon" ? "Polygon" : "Point"}</dd>
        {ann.shape !== "point" && (<><dt>Area</dt><dd className="readout">{Math.round(px).toLocaleString()} px²  <span className="faint">{(px * UM_PER_PX * UM_PER_PX).toFixed(0)} µm²</span></dd></>)}
        <dt>Vertices</dt><dd className="readout">{vertexCount(ann.geometry)}</dd>
        <dt>Source</dt><dd>{ann.source === "import" ? "Imported" : ann.source === "tool_assisted" ? "Assisted" : "Drawn"}</dd>
        <dt>ID</dt><dd className="readout faint">{ann.annotationId.slice(-8)}</dd>
      </dl>
      <div className="insp-head sub"><h4><Icon name="history" size={14} /> History</h4><span className="faint">Nothing is overwritten</span></div>
      <ol className="history">
        {history.map((v, i) => (
          <li key={v.rowId} className={i === 0 ? "current" : ""}>
            <span className="readout">v{v.version}</span>
            <span>
              {v.isDeleted ? "Deleted" : v.version === 1 ? (v.source === "import" ? "Imported" : "Created") : "Edited"}
              <span className="faint"> by {users.find((u) => u.id === v.createdBy)?.displayName ?? "unknown"}, {relativeTime(v.createdAt)}</span>
            </span>
            {i > 0 && !v.isDeleted && !readOnly && (
              <button className="btn ghost sm" onClick={() => onRestore(v)}>Restore</button>
            )}
          </li>
        ))}
      </ol>
    </section>
  );
}

function ShortcutsDialog({ onClose }: { onClose: () => void }) {
  const rows: [string, string][] = [
    ["V  H  B  P  R  D", "Select, pan, box, polygon, brush, point"],
    ["1 – 5", "Choose label (relabels the selection)"],
    ["Space (hold)", "Pan with any tool"],
    ["Alt (hold)", "Brush: switch add and remove while painting"],
    ["X", "Brush: toggle add or remove"],
    ["-  =", "Brush size"],
    ["Enter", "Close polygon"],
    ["Backspace", "Remove last polygon point while drawing"],
    ["Del", "Delete selected annotation"],
    ["Ctrl Z  /  Ctrl Shift Z", "Undo  /  redo (adds a version)"],
    ["F", "Fit image"],
    ["A", "Show or hide annotations"],
    ["[  ]", "Previous or next image"],
    ["Shift N", "Next image that needs work"],
    ["Ctrl Enter", "Submit for review"],
  ];
  return (
    <div className="scrim" onClick={onClose}>
      <div className="dialog" role="dialog" aria-label="Keyboard shortcuts" onClick={(e) => e.stopPropagation()}>
        <div className="panel-head"><h3>Keyboard shortcuts</h3><button className="btn ghost icon sm" onClick={onClose} aria-label="Close"><Icon name="close" size={16} /></button></div>
        <table className="grid shortcuts">
          <tbody>{rows.map(([k, d]) => <tr key={k}><td><kbd>{k}</kbd></td><td>{d}</td></tr>)}</tbody>
        </table>
      </div>
    </div>
  );
}

const SHAPE_NAME: Record<ShapeType, string> = { bbox: "a box", polygon: "a polygon", point: "a point" };
const shapeNames = (shapes: ShapeType[]) => shapes.map((x) => SHAPE_NAME[x]).join(" or ");
const fmt = (n: number) => Math.round(n).toLocaleString();
function initialsOf(users: { id: ID; initials: string }[], id: ID) {
  return users.find((u) => u.id === id)?.initials ?? "";
}

function SaveNote() {
  const { source, pendingWrites } = useStore();
  if (source === "sample") return <span className="dim">Sample data: changes are not saved</span>;
  return <span className="dim">{pendingWrites ? "Saving" : "All changes saved"}</span>;
}
