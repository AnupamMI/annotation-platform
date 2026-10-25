import { useMemo, useRef, useState } from "react";
import { api, type ConflictPolicy, type ExportFormat, type ImportPreview } from "../api";
import { useStore } from "../store";
import { relativeTime, useCan, useProject, userName } from "../hooks";
import { Icon } from "../components/Icon";
import { SampleImportPage } from "./ImportSample";
import type { ImportBatch, LabelClass } from "../types";

const STEPS = ["Upload", "Match images", "Map labels", "Check shapes", "Conflicts", "Review and import"];
const FORMAT_NAME: Record<ExportFormat, string> = { yolo: "YOLO", coco: "COCO JSON", cvat_xml: "CVAT XML", label_studio: "Label Studio JSON" };
const SHAPE_NAME: Record<string, string> = { bbox: "box", polygon: "polygon", point: "point" };

export function ImportPage() {
  const source = useStore((s) => s.source);
  return source === "api" ? <LiveImportPage /> : <SampleImportPage />;
}

/** Real import and export through the API. */
function LiveImportPage() {
  const project = useProject();
  const can = useCan();
  const store = useStore();
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [step, setStep] = useState(-1); // -1 = list
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [conflict, setConflict] = useState<ConflictPolicy>("keep_both");
  const [unlinked, setUnlinked] = useState<"skip" | "hold">("skip");
  const [progress, setProgress] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  const labels = useMemo(() => store.labels.filter((l) => l.projectId === project?.id), [store.labels, project]);
  const itemName = (id: string | null) => store.items.find((i) => i.id === id)?.name ?? "";
  const batches = store.imports.filter((b) => b.projectId === project?.id);

  if (!project) return null;

  const open = (p: ImportPreview) => {
    setPreview(p);
    // Only exact name matches are pre-selected. A looser match (e.g. "Atypical nucleus" vs "Nucleus")
    // could merge different findings, so a person has to choose it.
    setMapping(Object.fromEntries(p.report.labels.map((l) => [l.source, l.why.startsWith("Same name") ? l.suggestedId ?? "" : ""])));
    setConflict("keep_both");
    setUnlinked("skip");
    setError("");
    setStep(1);
  };

  const upload = async (file: File) => {
    setError("");
    setProgress(0);
    try {
      open(await api.startImport(project.id, file, setProgress));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setProgress(null);
    }
  };

  const resume = async (b: ImportBatch) => {
    try { open(await api.getImport(b.id)); } catch (err) { store.toast((err as Error).message, "error"); }
  };

  const cancel = async () => {
    if (preview) await api.cancelImport(preview.id).catch(() => undefined);
    await store.refreshProject(project.id);
    setPreview(null);
    setStep(-1);
  };

  const rollback = async (b: ImportBatch) => {
    try {
      await store.rollbackImport(b);
      store.toast(`Undid "${b.fileName}". Its ${b.annotations} annotations were marked deleted; their history is kept.`, "warn");
    } catch (err) {
      store.toast((err as Error).message, "error");
    }
  };

  if (step === -1) {
    return (
      <div className="page">
        <div className="page-head">
          <div>
            <h1>Import and export</h1>
            <p className="dim">Bring in annotations made in CVAT, Label Studio, YOLO or COCO, or export this project's annotations. Nothing is imported until you approve it, and every import can be undone.</p>
          </div>
          {can.importData && <button className="btn primary" onClick={() => { setPreview(null); setError(""); setStep(0); }}><Icon name="import" size={15} /> New import</button>}
        </div>

        <ExportPanel projectId={project.id} />

        <section className="panel">
          <div className="panel-head"><h3>Imports</h3></div>
          {batches.length === 0 ? <p className="panel-body dim">No imports yet.</p> : (
            <table className="grid">
              <thead><tr><th>File</th><th>Format</th><th className="num">Images</th><th className="num">Annotations</th><th>By</th><th>When</th><th>Status</th><th /></tr></thead>
              <tbody>
                {batches.map((b) => (
                  <tr key={b.id}>
                    <td className="readout">{b.fileName}</td>
                    <td>{FORMAT_NAME[b.format as ExportFormat] ?? b.format}</td>
                    <td className="num readout">{b.images}</td>
                    <td className="num readout">{b.annotations}</td>
                    <td>{userName(b.uploadedBy)}</td>
                    <td className="dim">{relativeTime(b.createdAt)}</td>
                    <td><span className={`tag ${b.status === "committed" ? "done" : b.status === "rolled_back" ? "new" : b.status === "awaiting_approval" ? "in_review" : "rejected"}`}>
                      {{ committed: "Imported", rolled_back: "Undone", awaiting_approval: "Waiting for approval", failed: "Failed" }[b.status]}
                    </span></td>
                    <td className="num">
                      {b.status === "committed" && can.importData && <button className="btn sm ghost" onClick={() => rollback(b)}><Icon name="rollback" size={14} /> Undo import</button>}
                      {b.status === "awaiting_approval" && can.importData && <button className="btn sm" onClick={() => resume(b)}>Continue</button>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      </div>
    );
  }

  const r = preview?.report;
  const unresolved = r ? r.labels.filter((l) => !mapping[l.source]).length : 0;
  const imported = r ? r.labels.filter((l) => mapping[l.source] && mapping[l.source] !== "skip").reduce((t, l) => t + l.count, 0) : 0;
  const shapeProblems = r ? r.labels.map((l) => ({ l, msg: compat(l.shapes, labels.find((x) => x.id === mapping[l.source])) })).filter((x) => x.msg?.blocking) : [];

  const next = () => {
    if (step === 2 && unresolved) { setError(`Choose what to do with ${unresolved === 1 ? "1 label" : `${unresolved} labels`} before continuing.`); return; }
    if (step === 2 && shapeProblems.length) { setError(shapeProblems[0].msg!.text); return; }
    setError("");
    setStep((s) => Math.min(5, s + 1));
  };

  const commit = async () => {
    if (!preview) return;
    setBusy(true);
    setError("");
    try {
      const out = await api.commitImport(preview.id, { labelMapping: mapping, conflictPolicy: conflict, unlinked });
      await store.refreshProject(project.id);
      const res = out.result;
      store.toast(`Imported ${res.annotations} annotations into ${res.images} images. They are waiting for review.`);
      setPreview(null);
      setStep(-1);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page import-page">
      <div className="page-head">
        <div>
          <h1>New import</h1>
          {r && <p className="dim"><span className="readout">{r.fileName}</span>, detected as {r.formatName}</p>}
        </div>
        <button className="btn ghost" onClick={cancel}>Cancel import</button>
      </div>

      <ol className="stepper" aria-label="Import steps">
        {STEPS.map((s, i) => (
          <li key={s} className={i === step ? "now" : i < step ? "done" : ""}>
            <span className="step-n readout">{i < step ? <Icon name="check" size={12} /> : i + 1}</span>{s}
          </li>
        ))}
      </ol>

      <section className="panel step-panel">
        {step === 0 && (
          <div className="dropzone" onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); const f = e.dataTransfer.files[0]; if (f) upload(f); }}>
            <Icon name="file" size={30} />
            <h3>Drop an export file here</h3>
            <p className="dim">CVAT XML (for images 1.1), Label Studio JSON, COCO JSON, or a YOLO folder as .zip. Put the images in the zip too if they aren't in the project yet.</p>
            {progress !== null ? (
              <div className="upload-progress"><span className="meter"><span style={{ width: `${Math.round(progress * 100)}%`, background: "var(--green)" }} /></span><span className="dim">{progress < 1 ? "Uploading" : "Reading the file"}</span></div>
            ) : (
              <div className="drop-actions"><button className="btn" onClick={() => inputRef.current?.click()}>Choose file</button></div>
            )}
            <input ref={inputRef} type="file" hidden accept=".zip,.json,.xml" onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(f); e.target.value = ""; }} />
          </div>
        )}

        {r && step === 1 && (
          <>
            <StepIntro title="Match images" text={`${r.totals.matched} of ${r.totals.images} images in the export match images in this project. Matching uses the file name first, then the exact file contents, then how the picture looks.`} />
            <table className="grid">
              <thead><tr><th>In the export</th><th>Matched to</th><th>How</th><th className="num">Shapes</th></tr></thead>
              <tbody>
                {r.images.map((m, i) => (
                  <tr key={m.file + i}>
                    <td className="readout">{m.file}</td>
                    <td className="readout">{m.itemId ? itemName(m.itemId) : <span className="warn-text">{m.reason}</span>}</td>
                    <td>{m.itemId ? <span className="dim">{m.how}</span> : <span className="warn-text">no match</span>}</td>
                    <td className="num readout">{m.shapes}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {r.totals.unmatched > 0 && (
              <div className="panel-body choice-row">
                <span>{r.totals.unmatched} {r.totals.unmatched === 1 ? "image isn't" : "images aren't"} in this project.</span>
                <label className="radio"><input type="radio" checked={unlinked === "skip"} onChange={() => setUnlinked("skip")} /> Skip</label>
                <label className={`radio${r.totals.unmatchedWithImage ? "" : " disabled"}`} title={r.totals.unmatchedWithImage ? "" : "The upload doesn't contain these images"}>
                  <input type="radio" disabled={!r.totals.unmatchedWithImage} checked={unlinked === "hold"} onChange={() => setUnlinked("hold")} />
                  Add the {r.totals.unmatchedWithImage || ""} included {r.totals.unmatchedWithImage === 1 ? "image" : "images"} as unlinked, to place in the hierarchy later
                </label>
              </div>
            )}
          </>
        )}

        {r && step === 2 && (
          <>
            <StepIntro title="Map labels" text="Each label in the export becomes one of this project's labels. Suggestions are only suggestions: nothing is dropped without your choice." />
            {r.labels.length === 0 ? <p className="panel-body dim">The export has no annotations on matched images.</p> : (
              <table className="grid">
                <thead><tr><th>Label in the export</th><th>Shapes</th><th>Becomes</th><th>Notes</th></tr></thead>
                <tbody>
                  {r.labels.map((l) => {
                    const target = labels.find((x) => x.id === mapping[l.source]);
                    const c = compat(l.shapes, target);
                    return (
                      <tr key={l.source}>
                        <td className="readout">{l.source}</td>
                        <td className="dim">{Object.entries(l.shapes).map(([k, n]) => `${n} ${SHAPE_NAME[k]}${n === 1 ? "" : k === "bbox" ? "es" : "s"}`).join(", ")}</td>
                        <td>
                          <select className="select" value={mapping[l.source] ?? ""} aria-label={`Map ${l.source}`}
                            onChange={(e) => setMapping((m) => ({ ...m, [l.source]: e.target.value }))}>
                            <option value="">Choose…</option>
                            {labels.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
                            <option value="skip">Don't import these</option>
                          </select>
                        </td>
                        <td>
                          {c ? <span className={c.blocking ? "error-text" : "warn-text"}>{c.text}</span>
                            : !mapping[l.source] && l.suggestedId ? <span className="warn-text">Possibly {labels.find((x) => x.id === l.suggestedId)?.name}. Choose to confirm.</span>
                            : !mapping[l.source] ? <span className="warn-text">{l.why}</span>
                            : mapping[l.source] === l.suggestedId ? <span className="dim">{l.why}</span> : <span className="dim">Your choice</span>}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </>
        )}

        {r && step === 3 && (
          <>
            <StepIntro title="Check shapes" text="Coordinates were converted to full-image pixels and every shape was checked." />
            {r.issues.length === 0 ? <p className="panel-body dim">Every shape is valid. Nothing needed fixing.</p> : (
              <ul className="check-list">
                {r.issues.map((i, k) => (
                  <li key={k} className={i.level === "fixed" ? "" : "warn"}>
                    <Icon name={i.level === "fixed" ? "check" : "alert"} size={15} /><span>{i.text}</span>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}

        {r && step === 4 && (
          <>
            <StepIntro title="Conflicts" text={r.conflicts.length ? `${r.conflicts.length} matched ${r.conflicts.length === 1 ? "image already has" : "images already have"} annotations. Existing work is never overwritten silently.` : "None of the matched images have annotations yet, so there is nothing to resolve."} />
            {r.conflicts.length > 0 && (
              <>
                <table className="grid">
                  <thead><tr><th>Image</th><th className="num">Already there</th><th className="num">In the export</th></tr></thead>
                  <tbody>{r.conflicts.map((c) => <tr key={c.itemId}><td className="readout">{c.name}</td><td className="num readout">{c.existing}</td><td className="num readout">{c.incoming}</td></tr>)}</tbody>
                </table>
                <fieldset className="choice-group panel-body">
                  {([
                    ["keep_both", "Keep both", "Imported shapes are added next to the existing ones. Useful for comparing annotators."],
                    ["replace", "Replace existing", "Existing shapes are marked deleted (their history stays) and the imported ones take their place."],
                    ["skip", "Skip these images", "Only images without annotations receive the import."],
                  ] as const).map(([k, t, d]) => (
                    <label key={k} className={`choice${conflict === k ? " on" : ""}`}>
                      <input type="radio" name="conflict" checked={conflict === k} onChange={() => setConflict(k)} />
                      <span><strong>{t}</strong><span className="dim">{d}</span></span>
                    </label>
                  ))}
                </fieldset>
              </>
            )}
          </>
        )}

        {r && step === 5 && (
          <>
            <StepIntro title="Review and import" text="This is what will be written. Imported annotations go to the review queue." />
            <dl className="summary">
              <dt>Images</dt><dd><span className="readout">{r.totals.matched}</span> matched{r.totals.unmatched ? <>, <span className="readout">{r.totals.unmatched}</span> {unlinked === "hold" ? "added as unlinked (those with images)" : "skipped"}</> : null}</dd>
              <dt>Annotations</dt><dd><span className="readout">{imported}</span> after label choices and checks</dd>
              <dt>Labels</dt><dd>{r.labels.map((l) => `${l.source} → ${mapping[l.source] === "skip" ? "not imported" : labels.find((x) => x.id === mapping[l.source])?.name}`).join(", ") || "None"}</dd>
              <dt>Conflicts</dt><dd>{r.conflicts.length ? { keep_both: "Keep both", replace: "Replace existing", skip: "Skip those images" }[conflict] : "None"}</dd>
              <dt>Undo</dt><dd>Available from the import list at any time</dd>
            </dl>
          </>
        )}

        {error && <p className="panel-body field-error">{error}</p>}

        {step > 0 && (
          <div className="dialog-foot">
            <button className="btn ghost" onClick={() => { setError(""); setStep((s) => s - 1); }} disabled={step === 1 || busy}>Back</button>
            {step < 5 ? <button className="btn primary" onClick={next}>Continue</button>
              : <button className="btn primary" onClick={commit} disabled={busy || imported === 0}>{busy ? "Importing" : `Import ${imported} annotations`}</button>}
          </div>
        )}
      </section>
    </div>
  );
}

function compat(shapes: Record<string, number>, target: LabelClass | undefined): { text: string; blocking: boolean } | null {
  if (!target) return null;
  for (const [shape, n] of Object.entries(shapes)) {
    if (target.allowedShapes.includes(shape as LabelClass["allowedShapes"][number])) continue;
    if (shape === "bbox" && target.allowedShapes.includes("polygon")) return { text: `${n} box(es) will become rectangular polygons.`, blocking: false };
    return { text: `${target.name} can't hold ${SHAPE_NAME[shape]}s. Choose another label or don't import these.`, blocking: true };
  }
  return null;
}

function ExportPanel({ projectId }: { projectId: string }) {
  const { items, toast } = useStore();
  const allowed = useCan().exportData;
  const [format, setFormat] = useState<ExportFormat>("yolo");
  const [scope, setScope] = useState<"approved" | "annotated" | "all">("approved");
  const [withImages, setWithImages] = useState(true);
  const [busy, setBusy] = useState(false);
  const its = items.filter((i) => i.projectId === projectId);
  const count = scope === "approved" ? its.filter((i) => i.status === "done").length
    : scope === "annotated" ? its.filter((i) => ["annotated", "in_review", "done"].includes(i.status)).length : its.length;

  const run = async () => {
    setBusy(true);
    try {
      const out = await api.exportProject(projectId, { format, scope, includeImages: withImages });
      toast(`Downloaded ${out.name}${out.notes ? `. See manifest.json for ${out.notes} note${out.notes > 1 ? "s" : ""} about what this format can't store.` : "."}`, out.notes ? "warn" : "ok");
    } catch (err) {
      toast((err as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel">
      <div className="panel-head"><div><h3>Export</h3><p className="dim">Current version of every annotation, with a manifest listing the exact versions included.</p></div></div>
      <div className="panel-body export-row">
        <label className="field"><span>Format</span>
          <select className="select" value={format} onChange={(e) => setFormat(e.target.value as ExportFormat)}>
            {Object.entries(FORMAT_NAME).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </label>
        <label className="field"><span>Images</span>
          <select className="select" value={scope} onChange={(e) => setScope(e.target.value as typeof scope)}>
            <option value="approved">Approved only</option>
            <option value="annotated">Annotated, in review or approved</option>
            <option value="all">All images</option>
          </select>
        </label>
        <label className="check export-check"><input type="checkbox" checked={withImages} onChange={(e) => setWithImages(e.target.checked)} /> Include image files</label>
        <button className="btn primary" onClick={run} disabled={busy || count === 0 || !allowed} title={!allowed ? "Admins and reviewers can export" : undefined}>
          <Icon name="import" size={15} /> {busy ? "Preparing" : `Export ${count} ${count === 1 ? "image" : "images"}`}
        </button>
      </div>
      {(format === "yolo" || format === "coco") && <p className="panel-body faint export-note">{FORMAT_NAME[format]} has no point shape, so point annotations are left out (listed in the manifest).</p>}
    </section>
  );
}

function StepIntro({ title, text }: { title: string; text: string }) {
  return (
    <div className="panel-head step-intro">
      <div><h3>{title}</h3><p className="dim">{text}</p></div>
    </div>
  );
}
