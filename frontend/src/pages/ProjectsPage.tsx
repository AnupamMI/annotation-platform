import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useStore } from "../store";
import { relativeTime, useCan } from "../hooks";
import { StatusMeter, countStatuses } from "../components/Charts";
import { Icon } from "../components/Icon";
import type { LabelClass } from "../types";

export function ProjectsPage() {
  const { projects, items, events } = useStore();
  const [creating, setCreating] = useState(false);
  const can = useCan();

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Projects</h1>
          <p className="dim">Each project has its own hierarchy, labels and team.</p>
        </div>
        {can.manage && <button className="btn primary" onClick={() => setCreating(true)}>
          <Icon name="plus" size={15} /> New project
        </button>}
      </div>

      <div className="panel project-list">
        {projects.map((p) => {
          const its = items.filter((i) => i.projectId === p.id);
          const c = countStatuses(its);
          const last = events.filter((e) => e.projectId === p.id).at(-1);
          return (
            <Link key={p.id} to={`/p/${p.id}`} className="project-row">
              <div className="project-main">
                <h3>{p.name}</h3>
                <p className="dim">{p.description}</p>
                <p className="faint project-levels">{p.levels.map((l) => l.name).join(" / ")}</p>
              </div>
              <div className="project-progress">
                {its.length ? (
                  <>
                    <div className="project-counts">
                      <span><span className="readout">{c.done}</span> <span className="dim">of {its.length} images done</span></span>
                      <span className="readout dim">{Math.round((c.done / its.length) * 100)}%</span>
                    </div>
                    <StatusMeter items={its} />
                  </>
                ) : (
                  <span className="dim">No images yet</span>
                )}
              </div>
              <div className="project-meta dim">{last ? `Active ${relativeTime(last.at)}` : "No activity yet"}</div>
              <Icon name="chevronRight" />
            </Link>
          );
        })}
      </div>

      {creating && <NewProjectDialog onClose={() => setCreating(false)} />}
    </div>
  );
}

const PRESETS: Record<string, { levels: string[]; labels: { name: string; color: string; shapes: LabelClass["allowedShapes"] }[]; hint: string }> = {
  cytology: {
    levels: ["Patient", "Slide", "Field of view"],
    labels: [
      { name: "Squamous cell", color: "#3fb59a", shapes: ["polygon", "bbox"] },
      { name: "Nucleus", color: "#7b80e0", shapes: ["polygon"] },
      { name: "Atypical nucleus", color: "#e47fa2", shapes: ["polygon"] },
    ],
    hint: "Patient / slide / field of view, splits by patient, nucleus must sit inside a cell.",
  },
  generic: {
    levels: ["Collection", "Image"],
    labels: [{ name: "Object", color: "#3fb59a", shapes: ["bbox", "polygon"] }],
    hint: "Collection / image. Rename levels and labels later in Setup.",
  },
};

function NewProjectDialog({ onClose }: { onClose: () => void }) {
  const { createProject, toast } = useStore();
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [pack, setPack] = useState<"cytology" | "generic">("cytology");
  const [error, setError] = useState("");

  const create = async () => {
    if (!name.trim()) { setError("Give the project a name."); return; }
    const preset = PRESETS[pack];
    setBusy(true);
    try {
      const project = await createProject({
        name: name.trim(),
        domainPack: pack,
        levels: preset.levels.map((n, i) => ({ name: n, isSplitLevel: i === 0 })),
        labels: preset.labels.map((l, i) => ({
          name: l.name, color: l.color, allowedShapes: l.shapes,
          mustBeInsideName: pack === "cytology" && i > 0 ? preset.labels[0].name : null,
        })),
      });
      toast(`Project "${project.name}" created.`);
      navigate(`/p/${project.id}`);
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  };

  return (
    <div className="scrim" onClick={onClose}>
      <div className="dialog" role="dialog" aria-label="New project" onClick={(e) => e.stopPropagation()}>
        <div className="panel-head"><h3>New project</h3><button className="btn ghost icon sm" onClick={onClose} aria-label="Close"><Icon name="close" size={16} /></button></div>
        <div className="panel-body form-stack">
          <label className="field">
            <span>Name</span>
            <input className="input" autoFocus value={name} onChange={(e) => { setName(e.target.value); setError(""); }} placeholder="e.g. LBC set B" />
            {error && <em className="field-error">{error}</em>}
          </label>
          <fieldset className="choice-group">
            <legend>Starting setup</legend>
            {(["cytology", "generic"] as const).map((k) => (
              <label key={k} className={`choice${pack === k ? " on" : ""}`}>
                <input type="radio" name="pack" checked={pack === k} onChange={() => setPack(k)} />
                <span>
                  <strong>{k === "cytology" ? "Cytology" : "General images"}</strong>
                  <span className="dim">{PRESETS[k].hint}</span>
                </span>
              </label>
            ))}
          </fieldset>
        </div>
        <div className="dialog-foot">
          <button className="btn ghost" onClick={onClose}>Cancel</button>
          <button className="btn primary" onClick={create} disabled={busy}>{busy ? "Creating" : "Create project"}</button>
        </div>
      </div>
    </div>
  );
}
