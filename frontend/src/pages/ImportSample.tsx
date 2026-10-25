import { useMemo, useRef, useState } from "react";
import { currentAnnotations, useStore } from "../store";
import { relativeTime, useProject, userName } from "../hooks";
import { Icon } from "../components/Icon";
import { closeRing } from "../lib/geometry";
import type { ID, ImportBatch, Item } from "../types";

/*
  Import flow, simulated on sample data. Each step mirrors docs/architecture.md section 4:
  inspect -> match images -> map labels -> validate -> conflicts -> approve -> commit (undoable).
*/

const STEPS = ["Upload", "Match images", "Map labels", "Check shapes", "Conflicts", "Review and import"];
const FORMAT_NAME: Record<ImportBatch["format"], string> = {
  yolo: "YOLO", coco: "COCO JSON", cvat_xml: "CVAT XML", label_studio: "Label Studio JSON",
};

type MatchRow = { file: string; itemId: ID | null; how: "file name" | "checksum" | "perceptual hash" | "no match" };
type LabelRow = { source: string; count: number; target: ID | "skip" | ""; why: string };

/** Sample mode (no backend): the same flow, simulated on in-memory data. */
export function SampleImportPage() {
  const project = useProject();
  const store = useStore();
  const { imports, items, labels, annotations } = store;
  const [step, setStep] = useState(-1); // -1 = list view
  const [file, setFile] = useState<{ name: string; format: ImportBatch["format"] } | null>(null);
  const [matches, setMatches] = useState<MatchRow[]>([]);
  const [labelRows, setLabelRows] = useState<LabelRow[]>([]);
  const [unlinked, setUnlinked] = useState<"skip" | "hold">("skip");
  const [conflict, setConflict] = useState<"keep_both" | "replace" | "skip">("keep_both");
  const [error, setError] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  const pItems = useMemo(() => items.filter((i) => i.projectId === project?.id), [items, project]);
  const pLabels = useMemo(() => labels.filter((l) => l.projectId === project?.id), [labels, project]);
  const batches = imports.filter((b) => b.projectId === project?.id);

  if (!project) return null;

  // targets for the simulated export: the last three images
  const targets: Item[] = pItems.slice(-3);

  const begin = (name: string) => {
    const lower = name.toLowerCase();
    const format: ImportBatch["format"] = lower.endsWith(".xml") ? "cvat_xml" : lower.includes("coco") ? "coco" : lower.endsWith(".zip") ? "yolo" : "label_studio";
    setFile({ name, format });
    setMatches([
      { file: "fov_10.jpg", itemId: targets[0]?.id ?? null, how: "file name" },
      { file: "fov_11.jpg", itemId: targets[1]?.id ?? null, how: "file name" },
      { file: "scan_0412.jpg", itemId: targets[2]?.id ?? null, how: "checksum" },
      { file: "IMG_2291_export.png", itemId: targets[2]?.id ?? null, how: "perceptual hash" },
      { file: "slide7_crop.png", itemId: null, how: "no match" },
    ]);
    const byName = (n: string) => pLabels.find((l) => l.name.toLowerCase().includes(n))?.id ?? "";
    setLabelRows([
      { source: "cell", count: 61, target: byName("squamous"), why: "Similar name" },
      { source: "nuclei", count: 58, target: byName("nucleus"), why: "Same word, plural" },
      { source: "HSIL_nucleus", count: 3, target: byName("atypical"), why: "HSIL is an atypical finding; please confirm" },
      { source: "rbc", count: 9, target: "", why: "No matching label in this project" },
    ]);
    setError("");
    setStep(1);
  };

  const unresolvedLabels = labelRows.filter((r) => r.target === "").length;
  const conflictItems = targets.filter((t) => currentAnnotations(annotations, t.id).length > 0);
  const mappedCount = labelRows.filter((r) => r.target && r.target !== "skip").reduce((t, r) => t + r.count, 0);

  const next = () => {
    if (step === 2 && unresolvedLabels) { setError(`Choose what to do with ${unresolvedLabels === 1 ? "1 label" : `${unresolvedLabels} labels`} before continuing.`); return; }
    setError("");
    setStep((s) => Math.min(STEPS.length, s + 1));
  };

  const commit = () => {
    const batchId = `ib-${Date.now().toString(36)}`;
    let written = 0;
    const touched = new Set<ID>();
    for (const t of targets) {
      const existing = currentAnnotations(useStore.getState().annotations, t.id);
      if (existing.length && conflict === "skip") continue;
      if (existing.length && conflict === "replace") existing.forEach((a) => store.deleteAnnotation(a.annotationId));
      const cells = store.cellsByItem[t.id] ?? [];
      const cellLabel = labelRows[0].target, nucLabel = labelRows[1].target, atyLabel = labelRows[2].target;
      for (const c of cells) {
        if (cellLabel && cellLabel !== "skip") {
          store.appendVersion({ annotationId: `a-${batchId}-${written}`, version: 1, itemId: t.id, labelId: cellLabel, shape: "polygon",
            geometry: { kind: "region", polygons: [[closeRing(c.cell)]] }, isDeleted: false, source: "import", importBatchId: batchId });
          written++;
        }
        const nl = c.abnormal ? atyLabel : nucLabel;
        if (nl && nl !== "skip") {
          store.appendVersion({ annotationId: `a-${batchId}-${written}`, version: 1, itemId: t.id, labelId: nl, shape: "polygon",
            geometry: { kind: "region", polygons: [[closeRing(c.nucleus)]] }, isDeleted: false, source: "import", importBatchId: batchId });
          written++;
        }
      }
      touched.add(t.id);
      store.setItemStatus(t.id, "in_review");
    }
    store.addImport({
      id: batchId, projectId: project.id, fileName: file!.name, format: file!.format, status: "committed",
      uploadedBy: store.currentUserId, createdAt: new Date().toISOString(), annotations: written, images: touched.size,
    });
    store.logEvent({ projectId: project.id, itemId: null, action: "import.committed", activeMs: 0, detail: file!.name });
    store.toast(`Imported ${written} annotations into ${touched.size} images. They are waiting for review.`);
    setStep(-1);
    setFile(null);
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
            <h1>Import</h1>
            <p className="dim">Bring in annotations made in CVAT, Label Studio, YOLO or COCO. Nothing is written until you approve it, and every import can be undone.</p>
          </div>
          <button className="btn primary" onClick={() => { setStep(0); setFile(null); }}><Icon name="import" size={15} /> New import</button>
        </div>
        <section className="panel">
          <div className="panel-head"><h3>Previous imports</h3></div>
          {batches.length === 0 ? <p className="panel-body dim">No imports yet.</p> : (
            <table className="grid">
              <thead><tr><th>File</th><th>Format</th><th className="num">Images</th><th className="num">Annotations</th><th>By</th><th>When</th><th>Status</th><th /></tr></thead>
              <tbody>
                {batches.map((b) => (
                  <tr key={b.id}>
                    <td className="readout">{b.fileName}</td>
                    <td>{FORMAT_NAME[b.format]}</td>
                    <td className="num readout">{b.images}</td>
                    <td className="num readout">{b.annotations}</td>
                    <td>{userName(b.uploadedBy)}</td>
                    <td className="dim">{relativeTime(b.createdAt)}</td>
                    <td><span className={`tag ${b.status === "committed" ? "done" : b.status === "rolled_back" ? "new" : "rejected"}`}>{b.status === "committed" ? "Imported" : b.status === "rolled_back" ? "Undone" : "Failed"}</span></td>
                    <td className="num">{b.status === "committed" && <button className="btn sm ghost" onClick={() => rollback(b)}><Icon name="rollback" size={14} /> Undo import</button>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      </div>
    );
  }

  return (
    <div className="page import-page">
      <div className="page-head">
        <div>
          <h1>New import</h1>
          {file && <p className="dim"><span className="readout">{file.name}</span>, detected as {FORMAT_NAME[file.format]}</p>}
        </div>
        <button className="btn ghost" onClick={() => setStep(-1)}>Cancel import</button>
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
          <div
            className="dropzone"
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => { e.preventDefault(); const f = e.dataTransfer.files[0]; if (f) begin(f.name); }}
          >
            <Icon name="file" size={30} />
            <h3>Drop an export file here</h3>
            <p className="dim">CVAT XML, Label Studio JSON, COCO JSON, or a YOLO folder as .zip. Images can be in the archive or already in the project.</p>
            <div className="drop-actions">
              <button className="btn" onClick={() => inputRef.current?.click()}>Choose file</button>
              <button className="btn ghost" onClick={() => begin("label_studio_set_B.json")}>Use the sample export</button>
            </div>
            <input ref={inputRef} type="file" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) begin(f.name); }} />
          </div>
        )}

        {step === 1 && (
          <>
            <StepIntro title="Match images" text="Each image in the export is matched to an image already in the project: first by file name, then by checksum, then by how the picture looks (perceptual hash) for renamed or re-saved files." />
            <table className="grid">
              <thead><tr><th>In the export</th><th>Matched to</th><th>How</th></tr></thead>
              <tbody>
                {matches.map((m) => (
                  <tr key={m.file}>
                    <td className="readout">{m.file}</td>
                    <td className="readout">{m.itemId ? pItems.find((i) => i.id === m.itemId)?.name : <span className="warn-text">Not in this project</span>}</td>
                    <td>{m.how === "no match" ? <span className="warn-text">{m.how}</span> : <span className="dim">{m.how}</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="panel-body choice-row">
              <span>1 image is not in this project.</span>
              <label className="radio"><input type="radio" checked={unlinked === "skip"} onChange={() => setUnlinked("skip")} /> Skip it</label>
              <label className="radio"><input type="radio" checked={unlinked === "hold"} onChange={() => setUnlinked("hold")} /> Keep it in an "unlinked" list to place later</label>
            </div>
            <p className="panel-body note"><Icon name="alert" size={14} /> Two export files match <span className="readout">{targets[2]?.name}</span>. Their annotations will be checked for duplicates in the next steps.</p>
          </>
        )}

        {step === 2 && (
          <>
            <StepIntro title="Map labels" text="Labels from the export are mapped to this project's labels. Suggestions are only suggestions: nothing is dropped without your choice." />
            <table className="grid">
              <thead><tr><th>Label in the export</th><th className="num">Shapes</th><th>Becomes</th><th>Why</th></tr></thead>
              <tbody>
                {labelRows.map((r, idx) => (
                  <tr key={r.source}>
                    <td className="readout">{r.source}</td>
                    <td className="num readout">{r.count}</td>
                    <td>
                      <select className="select" value={r.target} aria-label={`Map ${r.source}`}
                        onChange={(e) => setLabelRows((rows) => rows.map((x, j) => (j === idx ? { ...x, target: e.target.value } : x)))}>
                        <option value="">Choose…</option>
                        {pLabels.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
                        <option value="skip">Don't import these</option>
                      </select>
                    </td>
                    <td>{r.target === "" ? <span className="warn-text">{r.why}</span> : <span className="dim">{r.why}</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}

        {step === 3 && (
          <>
            <StepIntro title="Check shapes" text="Coordinates were converted to full-image pixels. These shapes needed attention:" />
            <ul className="check-list">
              <li><Icon name="check" size={15} /><span><strong>3 boxes</strong> extended past the image edge. They were trimmed to the edge.</span></li>
              <li><Icon name="check" size={15} /><span><strong>1 polygon</strong> crossed over itself. It was repaired into a valid outline.</span></li>
              <li><Icon name="check" size={15} /><span><strong>2 duplicate shapes</strong> came from the two files matched to the same image. One copy of each is kept.</span></li>
              <li className="warn"><Icon name="alert" size={15} /><span><strong>1 polygon</strong> has zero area and will not be imported.</span></li>
            </ul>
          </>
        )}

        {step === 4 && (
          <>
            <StepIntro title="Conflicts" text={conflictItems.length ? `${conflictItems.map((i) => i.name).join(", ")} already ${conflictItems.length === 1 ? "has" : "have"} annotations in this project. Existing work is never overwritten silently.` : "None of the matched images have annotations yet, so there is nothing to resolve."} />
            {conflictItems.length > 0 && (
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
            )}
          </>
        )}

        {step === 5 && (
          <>
            <StepIntro title="Review and import" text="This is what will be written. Imported annotations start as not reviewed and go to the review queue." />
            <dl className="summary">
              <dt>Images</dt><dd><span className="readout">{targets.length}</span> matched, <span className="readout">1</span> {unlinked === "skip" ? "skipped" : "kept as unlinked"}</dd>
              <dt>Annotations</dt><dd>about <span className="readout">{mappedCount}</span> after mapping and checks</dd>
              <dt>Labels</dt><dd>{labelRows.map((r) => `${r.source} → ${r.target === "skip" ? "not imported" : pLabels.find((l) => l.id === r.target)?.name}`).join(", ")}</dd>
              <dt>Conflicts</dt><dd>{conflictItems.length ? { keep_both: "Keep both", replace: "Replace existing", skip: "Skip those images" }[conflict] : "None"}</dd>
              <dt>Undo</dt><dd>Available from the import list at any time</dd>
            </dl>
          </>
        )}

        {error && <p className="panel-body field-error">{error}</p>}

        {step > 0 && (
          <div className="dialog-foot">
            <button className="btn ghost" onClick={() => setStep((s) => s - 1)}>Back</button>
            {step < 5 ? (
              <button className="btn primary" onClick={next}>Continue</button>
            ) : (
              <button className="btn primary" onClick={commit}>Import {mappedCount} annotations</button>
            )}
          </div>
        )}
      </section>
    </div>
  );
}

function StepIntro({ title, text }: { title: string; text: string }) {
  return (
    <div className="panel-head step-intro">
      <div><h3>{title}</h3><p className="dim">{text}</p></div>
    </div>
  );
}
