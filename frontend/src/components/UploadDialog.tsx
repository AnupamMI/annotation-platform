import { useRef, useState } from "react";
import { useStore } from "../store";
import { Icon } from "./Icon";
import type { Project } from "../types";

type Row = { file: File; state: "waiting" | "uploading" | "done" | "skipped" | "failed"; progress: number; note?: string };

/** Upload images into one place in the hierarchy, e.g. patient P-0104 / slide S-0104-A. */
export function UploadDialog({ project, defaultPath, onClose }: { project: Project; defaultPath: string[]; onClose: () => void }) {
  const { uploadImages, toast } = useStore();
  const containerLevels = project.levels.slice(0, -1);
  const [path, setPath] = useState<string[]>(containerLevels.map((_, i) => defaultPath[i] ?? ""));
  const [rows, setRows] = useState<Row[]>([]);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  const add = (files: FileList | null) => {
    if (!files) return;
    const imgs = [...files].filter((f) => /^image\//.test(f.type) || /\.(tiff?|bmp)$/i.test(f.name));
    setRows((r) => [...r, ...imgs.map((file) => ({ file, state: "waiting" as const, progress: 0 }))]);
    if (imgs.length < files.length) setError(`${files.length - imgs.length} file(s) were not images and were left out.`);
  };

  const patch = (i: number, p: Partial<Row>) => setRows((r) => r.map((x, j) => (j === i ? { ...x, ...p } : x)));

  const start = async () => {
    const missing = containerLevels.findIndex((_, i) => !path[i]?.trim());
    if (missing >= 0) { setError(`Enter the ${containerLevels[missing].name.toLowerCase()} ID.`); return; }
    if (!rows.some((r) => r.state === "waiting")) { setError("Add some images first."); return; }
    setError("");
    setRunning(true);
    let ok = 0;
    for (let i = 0; i < rows.length; i++) {
      if (rows[i].state !== "waiting") continue;
      patch(i, { state: "uploading" });
      try {
        const out = await uploadImages(project.id, rows[i].file, path.map((p) => p.trim()), (f) => patch(i, { progress: f }));
        if (out.created.length) { ok++; patch(i, { state: "done", progress: 1 }); }
        else patch(i, { state: "skipped", note: out.skipped[0]?.reason });
      } catch (err) {
        patch(i, { state: "failed", note: (err as Error).message });
      }
    }
    setRunning(false);
    if (ok) toast(`${ok} ${ok === 1 ? "image" : "images"} uploaded to ${path.join(" / ")}.`);
  };

  const done = rows.length > 0 && rows.every((r) => r.state !== "waiting" && r.state !== "uploading");

  return (
    <div className="scrim" onClick={() => !running && onClose()}>
      <div className="dialog upload-dialog" role="dialog" aria-label="Upload images" onClick={(e) => e.stopPropagation()}>
        <div className="panel-head">
          <h3>Upload images</h3>
          <button className="btn ghost icon sm" onClick={onClose} disabled={running} aria-label="Close"><Icon name="close" size={16} /></button>
        </div>
        <div className="panel-body form-stack">
          <div className="path-fields">
            {containerLevels.map((l, i) => (
              <label key={l.id} className="field">
                <span>{l.name} ID</span>
                <input className="input readout" value={path[i]} disabled={running} placeholder={i === 0 ? "e.g. P-0104" : "e.g. S-0104-A"}
                  onChange={(e) => setPath((p) => p.map((x, j) => (j === i ? e.target.value : x)))} />
              </label>
            ))}
          </div>
          <p className="faint">New IDs are created automatically. Use pseudonymous IDs only, never patient names.</p>

          <div
            className="dropzone small"
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => { e.preventDefault(); add(e.dataTransfer.files); }}
          >
            <Icon name="upload" size={22} />
            <span>Drop images here or <button className="linkish" onClick={() => inputRef.current?.click()} disabled={running}>choose files</button></span>
            <span className="faint">JPEG, PNG, WebP, TIFF or BMP. Duplicates are detected and skipped.</span>
            <input ref={inputRef} type="file" multiple accept="image/*,.tif,.tiff,.bmp" hidden onChange={(e) => add(e.target.files)} />
          </div>

          {rows.length > 0 && (
            <ul className="upload-list">
              {rows.map((r, i) => (
                <li key={i} className={r.state}>
                  <span className="readout up-name">{r.file.name}</span>
                  <span className="up-size faint">{r.file.size < 1e6 ? `${Math.max(1, Math.round(r.file.size / 1e3))} KB` : `${(r.file.size / 1e6).toFixed(1)} MB`}</span>
                  {r.state === "uploading" ? (
                    <span className="meter up-meter"><span style={{ width: `${Math.round(r.progress * 100)}%`, background: "var(--green)" }} /></span>
                  ) : (
                    <span className="up-state">{{ waiting: "Waiting", done: "Uploaded", skipped: `Skipped: ${r.note}`, failed: `Failed: ${r.note}` }[r.state]}</span>
                  )}
                </li>
              ))}
            </ul>
          )}
          {error && <p className="field-error">{error}</p>}
        </div>
        <div className="dialog-foot">
          {done ? (
            <button className="btn primary" onClick={onClose}>Done</button>
          ) : (
            <>
              <button className="btn ghost" onClick={onClose} disabled={running}>Cancel</button>
              <button className="btn primary" onClick={start} disabled={running}>
                {running ? "Uploading" : `Upload ${rows.filter((r) => r.state === "waiting").length || ""} ${rows.length === 1 ? "image" : "images"}`}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
