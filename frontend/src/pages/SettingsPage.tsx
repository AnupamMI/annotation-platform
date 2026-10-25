import { useEffect, useState, type FormEvent } from "react";
import { api, type GuidelineDoc, type TeamMember } from "../api";
import { useStore } from "../store";
import { relativeTime, useCan, useProject } from "../hooks";
import { Icon } from "../components/Icon";
import type { HierarchyLevel, LabelClass, ShapeType } from "../types";

const SHAPES: { id: ShapeType; name: string }[] = [
  { id: "polygon", name: "Polygon" },
  { id: "bbox", name: "Box" },
  { id: "point", name: "Point" },
];
const PALETTE = ["#3fb59a", "#7b80e0", "#e47fa2", "#e9a13b", "#9aa5b3", "#5fb3d9", "#c792ea"];
const ROLE_TEXT = { admin: "Admin", annotator: "Annotator", reviewer: "Reviewer", operator: "Scanner operator", viewer: "Viewer" };

export function SettingsPage() {
  const project = useProject();
  const { labels, users, saveSetup, toast, source } = useStore();
  const can = useCan();
  const [levels, setLevels] = useState<HierarchyLevel[]>([]);
  const [labs, setLabs] = useState<LabelClass[]>([]);
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    if (!project) return;
    setLevels(project.levels.map((l) => ({ ...l })));
    setLabs(labels.filter((l) => l.projectId === project.id).map((l) => ({ ...l })));
    setDirty(false);
  }, [project, labels]);

  if (!project) return null;

  const editLevel = (id: string, patch: Partial<HierarchyLevel>) => {
    setLevels((ls) => ls.map((l) => (l.id === id ? { ...l, ...patch } : patch.isSplitLevel ? { ...l, isSplitLevel: false } : l)));
    setDirty(true);
  };
  const editLabel = (id: string, patch: Partial<LabelClass>) => {
    setLabs((ls) => ls.map((l) => (l.id === id ? { ...l, ...patch } : l)));
    setDirty(true);
  };
  const addLabel = () => {
    const n = labs.length;
    setLabs((ls) => [...ls, {
      id: `${project.id}-lb-${Date.now().toString(36)}`, projectId: project.id, name: "New label",
      color: PALETTE[n % PALETTE.length], allowedShapes: ["polygon"], mustBeInsideId: null, hotkey: n < 9 ? String(n + 1) : "",
    }]);
    setDirty(true);
  };
  const save = () => {
    const bad = labs.find((l) => !l.name.trim() || l.allowedShapes.length === 0);
    if (bad) { toast(bad.name.trim() ? `${bad.name} needs at least one shape type.` : "Every label needs a name.", "error"); return; }
    saveSetup(project.id, levels, labs).then((ok) => { if (ok) toast("Setup saved."); });
  };

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Setup</h1>
          <p className="dim">How this project is organised and what can be labeled.</p>
        </div>
        {can.manage ? <button className="btn primary" onClick={save} disabled={!dirty}>{dirty ? "Save changes" : "Saved"}</button>
          : <span className="dim">Only admins can change the setup.</span>}
      </div>

      <section className="panel">
        <div className="panel-head">
          <div><h3>Hierarchy</h3><p className="dim">Images belong to the last level. Progress, filters and dataset splits follow these levels.</p></div>
        </div>
        <table className="grid">
          <thead><tr><th style={{ width: 70 }}>Level</th><th>Name</th><th>Split datasets by this level</th></tr></thead>
          <tbody>
            {levels.map((l) => (
              <tr key={l.id}>
                <td className="readout dim">{l.depth + 1}</td>
                <td><input className="input" value={l.name} onChange={(e) => editLevel(l.id, { name: e.target.value })} aria-label={`Level ${l.depth + 1} name`} /></td>
                <td>
                  <label className="radio">
                    <input type="radio" name="split" checked={l.isSplitLevel} onChange={() => editLevel(l.id, { isSplitLevel: true })} />
                    {l.isSplitLevel ? <span>Yes. One {l.name.toLowerCase()} never appears in two splits.</span> : <span className="dim">No</span>}
                  </label>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="panel">
        <div className="panel-head">
          <div><h3>Labels</h3><p className="dim">Shape types limit how each label can be drawn. "Must be inside" powers the rule checks.</p></div>
          <button className="btn sm" onClick={addLabel}><Icon name="plus" size={14} /> Add label</button>
        </div>
        <table className="grid labels-table">
          <thead><tr><th style={{ width: 54 }}>Key</th><th>Name</th><th>Color</th><th>Can be drawn as</th><th>Must be inside</th></tr></thead>
          <tbody>
            {labs.map((l) => (
              <tr key={l.id}>
                <td><kbd>{l.hotkey || "–"}</kbd></td>
                <td><input className="input" value={l.name} onChange={(e) => editLabel(l.id, { name: e.target.value })} aria-label="Label name" /></td>
                <td>
                  <div className="color-pick" role="radiogroup" aria-label={`${l.name} color`}>
                    {PALETTE.map((c) => (
                      <button key={c} role="radio" aria-checked={l.color === c} aria-label={c}
                        className={l.color === c ? "on" : ""} style={{ background: c }} onClick={() => editLabel(l.id, { color: c })} />
                    ))}
                  </div>
                </td>
                <td>
                  <div className="checks">
                    {SHAPES.map((s) => (
                      <label key={s.id} className="check">
                        <input type="checkbox" checked={l.allowedShapes.includes(s.id)}
                          onChange={(e) => editLabel(l.id, { allowedShapes: e.target.checked ? [...l.allowedShapes, s.id] : l.allowedShapes.filter((x) => x !== s.id) })} />
                        {s.name}
                      </label>
                    ))}
                  </div>
                </td>
                <td>
                  <select className="select" value={l.mustBeInsideId ?? ""} onChange={(e) => editLabel(l.id, { mustBeInsideId: e.target.value || null })} aria-label="Must be inside">
                    <option value="">No rule</option>
                    {labs.filter((x) => x.id !== l.id).map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
                  </select>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {source === "api" && <Guidelines projectId={project.id} canEdit={can.review} />}

      {source === "api" && can.manage ? <TeamManager /> : (
        <section className="panel">
          <div className="panel-head"><div><h3>Team</h3><p className="dim">{TRACKING_NOTE}</p></div></div>
          <table className="grid">
            <thead><tr><th>Person</th><th>Role</th><th>What they can do</th></tr></thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id}>
                  <td><span className="person"><span className="mini-avatar">{u.initials}</span>{u.displayName}</span></td>
                  <td>{ROLE_TEXT[u.role]}</td>
                  <td className="dim">{ROLE_CAN[u.role]}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  );
}

const TRACKING_NOTE = "Annotators are told that active time is recorded. It is used to find hard images, not to rank people.";
const ROLE_CAN: Record<string, string> = {
  admin: "Everything, including setup, team and imports",
  annotator: "Annotate images and submit them for review",
  reviewer: "Annotate, approve or request changes, assign work, export",
  operator: "Upload scans",
  viewer: "Look only",
};
type RoleName = keyof typeof ROLE_TEXT;

/** Guideline documents (SOPs) that Ask searches and cites. */
function Guidelines({ projectId, canEdit }: { projectId: string; canEdit: boolean }) {
  const toast = useStore((s) => s.toast);
  const [docs, setDocs] = useState<GuidelineDoc[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => { api.documents(projectId).then(setDocs).catch((e) => setError((e as Error).message)); }, [projectId]);

  const upload = async (file: File | undefined) => {
    if (!file) return;
    setBusy(true);
    setError("");
    try {
      const d = await api.uploadDocument(projectId, file, "");
      setDocs((x) => [...(x ?? []), d]);
      toast(`${d.title} added: ${d.sections} sections can now be searched from Ask.`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const remove = async (d: GuidelineDoc) => {
    try {
      await api.deleteDocument(d.id);
      setDocs((x) => x?.filter((y) => y.id !== d.id) ?? null);
      toast(`${d.title} removed.`);
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };

  return (
    <section className="panel">
      <div className="panel-head">
        <div><h3>Guidelines</h3><p className="dim">Your SOPs and annotation protocols. Ask quotes them with the section and page, and never decides a label for you.</p></div>
        {canEdit && (
          <label className={`btn sm${busy ? " disabled" : ""}`}>
            <Icon name="plus" size={14} /> {busy ? "Reading…" : "Add document"}
            <input type="file" accept=".md,.txt,.pdf" hidden disabled={busy} onChange={(e) => { upload(e.target.files?.[0]); e.target.value = ""; }} />
          </label>
        )}
      </div>
      {error && <p className="panel-body field-error">{error}</p>}
      {docs && docs.length === 0 && <p className="panel-body dim">No guidelines yet. {canEdit ? "Add a Markdown, text or PDF file." : "A reviewer or admin can add them."}</p>}
      {docs && docs.length > 0 && (
        <table className="grid">
          <thead><tr><th>Document</th><th className="num">Sections</th><th>Added</th><th /></tr></thead>
          <tbody>
            {docs.map((d) => (
              <tr key={d.id}>
                <td><span className="person"><Icon name="file" size={14} /><span>{d.title}<span className="faint team-email"> {d.fileName}</span></span></span></td>
                <td className="num readout">{d.sections}</td>
                <td className="dim">{relativeTime(d.createdAt)}</td>
                <td className="num">{canEdit && <button className="btn sm ghost" onClick={() => remove(d)}>Remove</button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

/** Admins: add people, change roles, reset passwords, deactivate. */
function TeamManager() {
  const { toast, currentUserId } = useStore();
  const [team, setTeam] = useState<TeamMember[] | null>(null);
  const [adding, setAdding] = useState(false);
  const [resetFor, setResetFor] = useState<TeamMember | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api.teamList().then(setTeam).catch((e) => setError((e as Error).message));
  }, []);

  const update = async (m: TeamMember, patch: Parameters<typeof api.teamUpdate>[1], done: string) => {
    try {
      const out = await api.teamUpdate(m.id, patch);
      setTeam((t) => t?.map((x) => (x.id === out.id ? out : x)) ?? null);
      useStore.setState((s) => ({ users: s.users.map((u) => (u.id === out.id ? { ...u, role: out.role, displayName: out.displayName } : u)) }));
      toast(done);
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };

  return (
    <section className="panel">
      <div className="panel-head">
        <div><h3>Team</h3><p className="dim">{TRACKING_NOTE}</p></div>
        <button className="btn sm" onClick={() => setAdding(true)}><Icon name="plus" size={14} /> Add person</button>
      </div>
      {error && <p className="panel-body field-error">{error}</p>}
      {team && (
        <table className="grid team-table">
          <thead><tr><th>Person</th><th>Role</th><th>Last sign-in</th><th>Status</th><th /></tr></thead>
          <tbody>
            {team.map((m) => (
              <tr key={m.id} className={m.isActive ? "" : "inactive"}>
                <td><span className="person"><span className="mini-avatar">{m.initials}</span><span>{m.displayName}<span className="faint team-email"> {m.email}</span></span></span></td>
                <td>
                  <select className="select" value={m.role} disabled={m.id === currentUserId} aria-label={`Role of ${m.displayName}`}
                    onChange={(e) => update(m, { role: e.target.value as RoleName }, `${m.displayName} is now ${ROLE_TEXT[e.target.value as RoleName].toLowerCase()}.`)}>
                    {Object.entries(ROLE_TEXT).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                  </select>
                </td>
                <td className="dim">{m.lastLoginAt ? relativeTime(m.lastLoginAt) : "Never"}</td>
                <td>{!m.isActive ? <span className="tag new">Deactivated</span> : !m.canSignIn ? <span className="tag in_review">No password yet</span> : <span className="tag done">Active</span>}</td>
                <td className="num team-actions">
                  <button className="btn sm ghost" onClick={() => setResetFor(m)}>{m.canSignIn ? "Reset password" : "Set password"}</button>
                  {m.id !== currentUserId && (
                    <button className="btn sm ghost" onClick={() => update(m, { isActive: !m.isActive }, m.isActive ? `${m.displayName} was deactivated and signed out.` : `${m.displayName} can sign in again.`)}>
                      {m.isActive ? "Deactivate" : "Reactivate"}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {adding && <AddPersonDialog onClose={() => setAdding(false)} onAdded={(m) => {
        setTeam((t) => [...(t ?? []), m]);
        useStore.setState((s) => ({ users: [...s.users, { id: m.id, displayName: m.displayName, initials: m.initials, role: m.role }] }));
      }} />}
      {resetFor && <PasswordDialog member={resetFor} onClose={() => setResetFor(null)}
        onSave={(pw) => update(resetFor, { password: pw }, `New password set for ${resetFor.displayName}. Tell them in person or by a separate channel.`).then(() => setResetFor(null))} />}
    </section>
  );
}

function AddPersonDialog({ onClose, onAdded }: { onClose: () => void; onAdded: (m: TeamMember) => void }) {
  const { toast } = useStore();
  const [f, setF] = useState({ displayName: "", email: "", role: "annotator" as RoleName, password: "" });
  const [error, setError] = useState("");
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!f.displayName.trim() || !f.email.trim()) { setError("Enter a name and an email."); return; }
    if (f.password.length < 8) { setError("The first password needs at least 8 characters."); return; }
    try {
      onAdded(await api.teamAdd(f));
      toast(`${f.displayName} was added. Share the password with them; they can change it after signing in.`);
      onClose();
    } catch (err) {
      setError((err as Error).message);
    }
  };
  return (
    <div className="scrim" onClick={onClose}>
      <form className="dialog" role="dialog" aria-label="Add person" onClick={(e) => e.stopPropagation()} onSubmit={submit} noValidate>
        <div className="panel-head"><h3>Add person</h3></div>
        <div className="panel-body form-stack">
          <label className="field"><span>Name</span><input className="input" autoFocus value={f.displayName} onChange={(e) => setF({ ...f, displayName: e.target.value })} /></label>
          <label className="field"><span>Email</span><input className="input" type="email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></label>
          <label className="field"><span>Role</span>
            <select className="select" value={f.role} onChange={(e) => setF({ ...f, role: e.target.value as RoleName })}>
              {Object.entries(ROLE_TEXT).map(([k, v]) => <option key={k} value={k}>{v}: {ROLE_CAN[k].toLowerCase()}</option>)}
            </select>
          </label>
          <label className="field"><span>First password</span>
            <input className="input" type="text" autoComplete="off" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} />
            <em className="faint field-hint">At least 8 characters. They can change it after signing in.</em>
          </label>
          {error && <p className="field-error" role="alert">{error}</p>}
        </div>
        <div className="dialog-foot">
          <button type="button" className="btn ghost" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn primary">Add person</button>
        </div>
      </form>
    </div>
  );
}

function PasswordDialog({ member, onClose, onSave }: { member: TeamMember; onClose: () => void; onSave: (pw: string) => void }) {
  const [pw, setPw] = useState("");
  const [error, setError] = useState("");
  return (
    <div className="scrim" onClick={onClose}>
      <form className="dialog" role="dialog" aria-label="Set password" onClick={(e) => e.stopPropagation()}
        onSubmit={(e) => { e.preventDefault(); if (pw.length < 8) { setError("Use at least 8 characters."); return; } onSave(pw); }} noValidate>
        <div className="panel-head"><h3>New password for {member.displayName}</h3></div>
        <div className="panel-body form-stack">
          <label className="field"><span>Password</span><input className="input" type="text" autoComplete="off" autoFocus value={pw} onChange={(e) => { setPw(e.target.value); setError(""); }} /></label>
          <p className="faint">They will be signed out everywhere and need this password to sign in again.</p>
          {error && <p className="field-error" role="alert">{error}</p>}
        </div>
        <div className="dialog-foot">
          <button type="button" className="btn ghost" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn primary">Set password</button>
        </div>
      </form>
    </div>
  );
}
