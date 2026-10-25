/*
  Ask panel.
  - With the backend: questions go to the server, which answers with read-only
    queries (written by a language model when one is configured, fixed queries
    otherwise) and guideline search. Agents return a plan that a person approves;
    the server runs it and logs every step.
  - Sample mode: answers are computed here from the sample data.
  Agents never change annotations either way.
*/

import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { currentAnnotations, useStore } from "../store";
import { formatDuration, STATUS_LABEL, useCan } from "../hooks";
import { api, type AgentRunOut, type AiStatus, type AskAnswer } from "../api";
import { bounds, containmentProblems } from "../lib/geometry";
import { Icon } from "./Icon";
import type { ID } from "../types";

type Table = { cols: string[]; rows: (string | number)[][] };
type PlanStep = { text: string; detail?: string };
type Msg =
  | { id: number; kind: "user"; text: string }
  | { id: number; kind: "answer"; text: string; sql?: string; table?: Table; source?: string; links?: { label: string; to: string }[] }
  | { id: number; kind: "live"; answer: AskAnswer }
  | { id: number; kind: "run"; run: AgentRunOut; busy?: boolean }
  | { id: number; kind: "thinking" }
  | { id: number; kind: "error"; text: string }
  | { id: number; kind: "plan"; agent: string; summary: string; steps: PlanStep[]; warning?: string; status: "awaiting" | "running" | "done" | "cancelled"; at: number; result?: string; findings?: { label: string; to: string; note: string }[] };

const SUGGESTIONS = [
  "How many images are left?",
  "Average time per image by annotator",
  "Which images have rule warnings?",
  "What does our guideline say about atypical nuclei?",
  "Build a training dataset split by patient",
  "Run a consistency check",
];

let seq = 1;

export function AskPanel({ projectId, onClose }: { projectId: ID; onClose: () => void }) {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [text, setText] = useState("");
  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const live = useStore((s) => s.source === "api");
  const [status, setStatus] = useState<AiStatus | null>(null);

  useEffect(() => { inputRef.current?.focus(); }, []);
  useEffect(() => { if (live) api.aiStatus().then(setStatus).catch(() => setStatus(null)); }, [live]);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [msgs]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const ask = (q: string) => {
    const question = q.trim();
    if (!question) return;
    setText("");
    if (!live) {
      setMsgs((m) => [...m, { id: seq++, kind: "user", text: question }, answerFor(question, projectId)]);
      return;
    }
    const wait = seq++;
    setMsgs((m) => [...m, { id: seq++, kind: "user", text: question }, { id: wait, kind: "thinking" }]);
    api.ask(projectId, question).then(
      (reply) => setMsgs((m) => m.map((x) => x.id !== wait ? x : reply.kind === "plan" ? { id: wait, kind: "run", run: reply.run } : { id: wait, kind: "live", answer: reply })),
      (err: Error) => setMsgs((m) => m.map((x) => (x.id === wait ? { id: wait, kind: "error", text: err.message } : x))),
    );
  };

  const setRun = (id: number, run: AgentRunOut, busy = false) =>
    setMsgs((m) => m.map((x) => (x.id === id && x.kind === "run" ? { ...x, run, busy } : x)));
  const decide = (m: Extract<Msg, { kind: "run" }>, approve: boolean) => {
    setRun(m.id, approve ? { ...m.run, status: "running" } : m.run, true);
    (approve ? api.approveRun(m.run.id) : api.cancelRun(m.run.id)).then(
      (run) => {
        setRun(m.id, run);
        if (run.kind === "dataset_builder" && run.status === "completed") useStore.getState().toast("The dataset is ready to download.");
      },
      (err: Error) => { setRun(m.id, m.run); useStore.getState().toast(err.message, "error"); },
    );
  };

  const updatePlan = (id: number, patch: Partial<Extract<Msg, { kind: "plan" }>>) =>
    setMsgs((m) => m.map((x) => (x.id === id && x.kind === "plan" ? { ...x, ...patch } : x)));

  const approve = (p: Extract<Msg, { kind: "plan" }>) => {
    updatePlan(p.id, { status: "running", at: 0 });
    p.steps.forEach((_, i) => setTimeout(() => updatePlan(p.id, { at: i + 1 }), 650 * (i + 1)));
    setTimeout(() => {
      const outcome = runAgent(p.agent, projectId);
      updatePlan(p.id, { status: "done", at: p.steps.length, result: outcome.result, findings: outcome.findings });
    }, 650 * p.steps.length + 300);
  };

  return (
    <aside className="ask" role="dialog" aria-label="Ask about this project">
      <div className="ask-head">
        <div>
          <h3>Ask about this project</h3>
          <p className="faint">Answers come from read-only queries. Agents never change annotations.</p>
          {live && status && !status.freeForm && (
            <p className="faint ask-note">No language model is set up, so I answer common questions with fixed queries. Guideline search and agents still work.</p>
          )}
        </div>
        <button className="btn ghost icon sm" onClick={onClose} aria-label="Close"><Icon name="close" size={16} /></button>
      </div>

      <div className="ask-body">
        {msgs.length === 0 && (
          <div className="ask-start">
            <p className="dim">Try one of these, or ask in your own words.</p>
            <div className="chips">
              {SUGGESTIONS.map((s) => <button key={s} className="chip" onClick={() => ask(s)}>{s}</button>)}
            </div>
          </div>
        )}
        {msgs.map((m) => {
          if (m.kind === "user") return <div key={m.id} className="msg user">{m.text}</div>;
          if (m.kind === "thinking") return <div key={m.id} className="msg answer faint">Working on it…</div>;
          if (m.kind === "error") return <div key={m.id} className="msg answer"><p className="error-text">{m.text}</p></div>;
          if (m.kind === "live") return <LiveAnswer key={m.id} a={m.answer} projectId={projectId} />;
          if (m.kind === "run") return <RunCard key={m.id} m={m} projectId={projectId} onDecide={decide} onChange={() => { setText(`${m.run.request}, but `); inputRef.current?.focus(); }} />;
          if (m.kind === "answer") return (
            <div key={m.id} className="msg answer">
              <p>{m.text}</p>
              {m.table && (
                <table className="grid mini">
                  <thead><tr>{m.table.cols.map((c, i) => <th key={c} className={i > 0 ? "num" : ""}>{c}</th>)}</tr></thead>
                  <tbody>{m.table.rows.map((r, i) => <tr key={i}>{r.map((v, j) => <td key={j} className={j > 0 ? "num readout" : ""}>{v}</td>)}</tr>)}</tbody>
                </table>
              )}
              {m.links && <div className="msg-links">{m.links.map((l) => <Link key={l.to + l.label} to={l.to} className="readout">{l.label}</Link>)}</div>}
              {m.source && <p className="msg-source"><Icon name="file" size={13} /> {m.source}</p>}
              {m.sql && (
                <details className="sql">
                  <summary>Query used</summary>
                  <pre className="readout">{m.sql}</pre>
                </details>
              )}
            </div>
          );
          return (
            <div key={m.id} className={`msg plan ${m.status}`}>
              <div className="plan-head">
                <span className="plan-agent">{m.agent}</span>
                <span className="faint">{m.status === "awaiting" ? "Waiting for your approval" : m.status === "running" ? "Running" : m.status === "done" ? "Finished" : "Cancelled"}</span>
              </div>
              <p>{m.summary}</p>
              <ol className="plan-steps">
                {m.steps.map((s, i) => (
                  <li key={i} className={m.status !== "awaiting" && i < m.at ? "ok" : m.status === "running" && i === m.at ? "now" : ""}>
                    <span className="readout step-n">{m.status !== "awaiting" && i < m.at ? <Icon name="check" size={12} /> : i + 1}</span>
                    <span>{s.text}{s.detail && <span className="faint"> {s.detail}</span>}</span>
                  </li>
                ))}
              </ol>
              {m.warning && <p className="plan-warn"><Icon name="alert" size={14} /> {m.warning}</p>}
              {m.status === "awaiting" && (
                <div className="plan-actions">
                  <button className="btn sm ghost" onClick={() => updatePlan(m.id, { status: "cancelled" })}>Cancel</button>
                  <button className="btn sm" onClick={() => { setText(`${m.agent}: change `); inputRef.current?.focus(); }}>Change plan</button>
                  <button className="btn sm primary" onClick={() => approve(m)}>Approve and run</button>
                </div>
              )}
              {m.result && <p className="plan-result">{m.result}</p>}
              {m.findings && m.findings.length > 0 && (
                <ul className="findings">
                  {m.findings.map((f, i) => <li key={i}><Link to={f.to} className="readout">{f.label}</Link><span className="dim">{f.note}</span></li>)}
                </ul>
              )}
            </div>
          );
        })}
        <div ref={endRef} />
      </div>

      <form className="ask-compose" onSubmit={(e) => { e.preventDefault(); ask(text); }}>
        <textarea
          ref={inputRef}
          className="textarea"
          rows={2}
          placeholder="Ask about progress, time, labels, or ask an agent to build a dataset"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(text); } }}
        />
        <button className="btn primary icon" type="submit" aria-label="Send" disabled={!text.trim()}><Icon name="send" size={16} /></button>
      </form>
    </aside>
  );
}

// ---------------- server answers and plans ----------------

function LiveAnswer({ a, projectId }: { a: AskAnswer; projectId: ID }) {
  return (
    <div className="msg answer">
      <p>{a.text}</p>
      {a.table && a.table.rows.length > 0 && (
        <table className="grid mini">
          <thead><tr>{a.table.cols.map((c, i) => <th key={c + i} className={i > 0 ? "num" : ""}>{c}</th>)}</tr></thead>
          <tbody>{a.table.rows.map((r, i) => <tr key={i}>{r.map((v, j) => <td key={j} className={j > 0 ? "num readout" : ""}>{v ?? "—"}</td>)}</tr>)}</tbody>
        </table>
      )}
      {a.table?.truncated && <p className="faint">Showing the first {a.table.rows.length} rows.</p>}
      {a.links.length > 0 && (
        <div className="msg-links">{a.links.map((l) => <Link key={l.itemId} to={`/p/${projectId}/annotate/${l.itemId}`} className="readout">{l.label}</Link>)}</div>
      )}
      {a.sources.map((s, i) => (
        <p key={i} className="msg-source" title={s.excerpt}>
          <Icon name="file" size={13} /> [{i + 1}] {s.title}, {s.section}{s.page ? `, page ${s.page}` : ""}
        </p>
      ))}
      {a.sql && (
        <details className="sql">
          <summary>Query used{a.via === "model" ? " (written by the language model, checked before running)" : ""}</summary>
          <pre className="readout">{a.sql}</pre>
        </details>
      )}
    </div>
  );
}

const RUN_STATUS: Record<AgentRunOut["status"], string> = {
  awaiting_approval: "Waiting for approval",
  running: "Running",
  completed: "Finished",
  cancelled: "Cancelled",
  failed: "Failed",
};

function RunCard({ m, projectId, onDecide, onChange }: {
  m: Extract<Msg, { kind: "run" }>;
  projectId: ID;
  onDecide: (m: Extract<Msg, { kind: "run" }>, approve: boolean) => void;
  onChange: () => void;
}) {
  const { run, busy } = m;
  const can = useCan();
  const me = useStore((s) => s.currentUserId);
  const mayApprove = (run.kind !== "dataset_builder" || can.review) && (run.userId === me || can.manage);
  const finished = run.status === "completed";
  const css = run.status === "awaiting_approval" ? "awaiting" : run.status === "completed" ? "done" : run.status;
  return (
    <div className={`msg plan ${css}`}>
      <div className="plan-head">
        <span className="plan-agent">{run.agent}</span>
        <span className="faint">{RUN_STATUS[run.status]}</span>
      </div>
      <p>{run.summary}</p>
      <ol className="plan-steps">
        {run.steps.map((s, i) => (
          <li key={i} className={finished ? "ok" : run.status === "running" && i === 0 ? "now" : ""}>
            <span className="readout step-n">{finished ? <Icon name="check" size={12} /> : i + 1}</span>
            <span>{s.text}{s.detail && <span className="faint"> ({s.detail})</span>}</span>
          </li>
        ))}
      </ol>
      {run.warning && <p className="plan-warn"><Icon name="alert" size={14} /> {run.warning}</p>}
      {run.status === "awaiting_approval" && (
        <div className="plan-actions">
          <button className="btn sm ghost" disabled={busy} onClick={() => onDecide(m, false)}>Cancel</button>
          <button className="btn sm" disabled={busy} onClick={onChange}>Change plan</button>
          {mayApprove
            ? <button className="btn sm primary" disabled={busy} onClick={() => onDecide(m, true)}>Approve and run</button>
            : <span className="faint">{run.kind === "dataset_builder" ? "A reviewer or admin needs to approve this." : "Only the person who asked can approve this."}</span>}
        </div>
      )}
      {run.error && <p className="error-text">{run.error}</p>}
      {run.result.text && <p className="plan-result">{run.result.text}</p>}
      {run.result.findings && run.result.findings.length > 0 && (
        <ul className="findings">
          {run.result.findings.map((f, i) => (
            <li key={i}><Link to={`/p/${projectId}/annotate/${f.itemId}`} className="readout">{f.imageName}</Link><span className="dim">{f.note}</span></li>
          ))}
        </ul>
      )}
      {run.result.download && (
        can.exportData
          ? <button className="btn sm" onClick={() => api.download(run.result.download!).catch((e: Error) => useStore.getState().toast(e.message, "error"))}>
              <Icon name="download" size={14} /> Download dataset
            </button>
          : <p className="faint">A reviewer or admin can download it from the Ask panel or Export.</p>
      )}
      {(finished || run.status === "failed") && run.toolCalls.length > 0 && (
        <details className="sql">
          <summary>What it did</summary>
          <pre className="readout">{run.toolCalls.map((c) => `${c.tool}${c.summary ? `  ${c.summary}` : ""}${c.ms !== undefined ? `  ${c.ms} ms` : ""}${c.error ? `  error: ${c.error}` : ""}`).join("\n")}</pre>
        </details>
      )}
    </div>
  );
}

// ---------------- sample mode answers (computed from the current data) ----------------

function answerFor(q: string, projectId: ID): Msg {
  const s = useStore.getState();
  const items = s.items.filter((i) => i.projectId === projectId);
  const lower = q.toLowerCase();
  const id = seq++;

  if (/dataset|split|train|export/.test(lower)) return datasetPlan(id, projectId);
  if (/consisten|run .*check|audit/.test(lower)) {
    return {
      id, kind: "plan", agent: "Consistency checker", status: "awaiting", at: 0,
      summary: `I'll check all ${items.length} images against the project rules and flag anything that needs a person to look. I won't change any annotation.`,
      steps: [
        { text: "Load the current version of every annotation" },
        { text: "Check containment rules", detail: "(nucleus inside a cell)" },
        { text: "Look for near-duplicate shapes", detail: "(same label, almost the same outline)" },
        { text: "Find approved images that have no annotations" },
        { text: "List what needs attention, with links" },
      ],
    };
  }
  if (/left|remain|how many|progress|status/.test(lower)) {
    const counts = new Map<string, number>();
    items.forEach((i) => counts.set(i.status, (counts.get(i.status) ?? 0) + 1));
    const need = items.filter((i) => ["new", "in_progress", "rejected"].includes(i.status)).length;
    const review = counts.get("in_review") ?? 0;
    return {
      id, kind: "answer",
      text: `${need} of ${items.length} images still need annotation, and ${review} ${review === 1 ? "is" : "are"} waiting for review.`,
      table: { cols: ["Status", "Images"], rows: [...counts.entries()].sort((a, b) => b[1] - a[1]).map(([k, v]) => [STATUS_LABEL[k], v]) },
      sql: `SELECT status, count(*) AS images\nFROM v_item_progress\nWHERE project_id = :project_id\nGROUP BY status\nORDER BY images DESC;`,
    };
  }
  if (/time|slow|fast|per image|annotator|who/.test(lower)) {
    const rows = s.users.map((u) => {
      const ev = s.events.filter((e) => e.projectId === projectId && e.userId === u.id && e.action === "item.submitted");
      return { u, n: ev.length, avg: ev.length ? ev.reduce((t, e) => t + e.activeMs, 0) / ev.length : 0 };
    }).filter((r) => r.n > 0).sort((a, b) => b.avg - a.avg);
    const slowest = rows[0];
    return {
      id, kind: "answer",
      text: slowest ? `Over the last 14 days, average active time per submitted image ranges from ${formatDuration(rows.at(-1)!.avg)} to ${formatDuration(slowest.avg)}. Time stops counting after a minute without input, so breaks are not included.` : "No images have been submitted yet.",
      table: { cols: ["Annotator", "Images", "Avg active time"], rows: rows.map((r) => [r.u.displayName, r.n, formatDuration(r.avg)]) },
      sql: `SELECT u.display_name, count(*) AS images,\n       avg(e.active_ms) AS avg_active_ms\nFROM v_annotator_time e\nJOIN users u ON u.id = e.user_id\nWHERE e.project_id = :project_id\n  AND e.action = 'item.submitted'\n  AND e.created_at > now() - interval '14 days'\nGROUP BY u.display_name\nORDER BY avg_active_ms DESC;`,
    };
  }
  if (/warning|rule|outside|inside/.test(lower)) {
    const rules = new Map(s.labels.filter((l) => l.projectId === projectId && l.mustBeInsideId).map((l) => [l.id, l.mustBeInsideId!]));
    const hits = items.map((i) => ({ i, n: containmentProblems(currentAnnotations(s.annotations, i.id), rules).length })).filter((x) => x.n > 0);
    return {
      id, kind: "answer",
      text: hits.length ? `${hits.length} ${hits.length === 1 ? "image has" : "images have"} shapes that break a containment rule.` : "No image breaks a containment rule right now.",
      table: hits.length ? { cols: ["Image", "Shapes to check"], rows: hits.map((h) => [h.i.name, h.n]) } : undefined,
      links: hits.map((h) => ({ label: `Open ${h.i.name}`, to: `/p/${projectId}/annotate/${h.i.id}` })),
      sql: `SELECT a.item_id, count(*) AS shapes_to_check\nFROM v_current_annotations a\nJOIN label_classes lc ON lc.id = a.label_class_id\nWHERE lc.must_be_inside_id IS NOT NULL\n  AND NOT EXISTS (\n    SELECT 1 FROM v_current_annotations p\n    WHERE p.item_id = a.item_id\n      AND p.label_class_id = lc.must_be_inside_id\n      AND ST_Within(a.geometry, p.geometry))\nGROUP BY a.item_id;`,
    };
  }
  if (/guideline|protocol|asc|lsil|hsil|atypical|how should|rule for/.test(lower)) {
    return {
      id, kind: "answer",
      text: "The sample guideline says: use Atypical nucleus only when the nucleus is noticeably enlarged or darker than the normal nuclei around it. If you are unsure, label it Nucleus and leave a note for the reviewer instead of guessing.",
      source: "Sample guideline v0.1, section 3.2 \"Atypical nuclei\". Placeholder text until your lab's real SOP is uploaded.",
    };
  }
  return {
    id, kind: "answer",
    text: "I can answer questions about progress, time, labels and rule checks, look things up in your guidelines, and run agents that build datasets or check consistency. Try one of the suggestions.",
  };
}

function patientOf(itemId: ID) {
  const s = useStore.getState();
  const it = s.items.find((i) => i.id === itemId);
  let n = s.nodes.find((x) => x.id === it?.nodeId);
  while (n?.parentId) n = s.nodes.find((x) => x.id === n!.parentId);
  return n?.externalId ?? "?";
}

function datasetPlan(id: number, projectId: ID): Msg {
  const s = useStore.getState();
  const items = s.items.filter((i) => i.projectId === projectId);
  const done = items.filter((i) => i.status === "done");
  const pending = items.filter((i) => i.status === "in_review").length;
  const byPatient = new Map<string, number>();
  done.forEach((i) => byPatient.set(patientOf(i.id), (byPatient.get(patientOf(i.id)) ?? 0) + 1));
  const patients = [...byPatient.entries()].sort((a, b) => b[1] - a[1]);
  const splitText = patients.map(([p, n], i) => `${p} (${n}) to ${i === 0 ? "train" : i === 1 ? "validation" : "test"}`).join(", ");
  return {
    id, kind: "plan", agent: "Dataset builder", status: "awaiting", at: 0,
    summary: `I'll build a segmentation dataset from the ${done.length} approved images, keeping each patient in only one split so nothing leaks between train and test.`,
    steps: [
      { text: `Select approved images`, detail: `(${done.length} images; ${pending} still in review are left out)` },
      { text: "Freeze the exact annotation versions", detail: "(so the dataset can be rebuilt identically later)" },
      { text: "Split by patient", detail: `(${splitText || "no patients yet"})` },
      { text: "Export as YOLO segmentation", detail: "(cell, nucleus, atypical nucleus)" },
      { text: "Write the manifest and lineage record" },
    ],
    warning: patients.length < 3
      ? `Only ${patients.length} ${patients.length === 1 ? "patient has" : "patients have"} approved images, so there will be no test split. A 70/15/15 split by patient needs approved images from more patients.`
      : undefined,
  };
}

function runAgent(agent: string, projectId: ID): { result: string; findings?: { label: string; to: string; note: string }[] } {
  const s = useStore.getState();
  const items = s.items.filter((i) => i.projectId === projectId);
  if (agent === "Dataset builder") {
    const done = items.filter((i) => i.status === "done");
    const n = done.reduce((t, i) => t + currentAnnotations(s.annotations, i.id).length, 0);
    s.logEvent({ projectId, itemId: null, action: "dataset.created", activeMs: 0, detail: "lbc-a-seg-v1" });
    s.toast("Dataset lbc-a-seg-v1 is ready to download.");
    return { result: `Dataset "lbc-a-seg-v1" created: ${done.length} images, ${n} annotations, frozen at their current versions. The manifest lists every image, annotation version and split.` };
  }
  const rules = new Map(s.labels.filter((l) => l.projectId === projectId && l.mustBeInsideId).map((l) => [l.id, l.mustBeInsideId!]));
  const findings: { label: string; to: string; note: string }[] = [];
  for (const i of items) {
    const anns = currentAnnotations(s.annotations, i.id);
    const bad = containmentProblems(anns, rules).length;
    if (bad) findings.push({ label: i.name, to: `/p/${projectId}/annotate/${i.id}`, note: `${bad} ${bad === 1 ? "shape breaks" : "shapes break"} a containment rule` });
    let dups = 0;
    for (let a = 0; a < anns.length; a++) for (let b = a + 1; b < anns.length; b++) {
      if (anns[a].labelId !== anns[b].labelId) continue;
      const A = bounds(anns[a].geometry), B = bounds(anns[b].geometry);
      if (Math.abs(A.x - B.x) < 3 && Math.abs(A.y - B.y) < 3 && Math.abs(A.w - B.w) < 3 && Math.abs(A.h - B.h) < 3) dups++;
    }
    if (dups) findings.push({ label: i.name, to: `/p/${projectId}/annotate/${i.id}`, note: `${dups} possible duplicate ${dups === 1 ? "shape" : "shapes"}` });
    if (i.status === "done" && anns.length === 0) findings.push({ label: i.name, to: `/p/${projectId}/annotate/${i.id}`, note: "approved but has no annotations" });
  }
  return {
    result: findings.length ? `Checked ${items.length} images. ${findings.length} ${findings.length === 1 ? "thing needs" : "things need"} a person to look. No annotations were changed.` : `Checked ${items.length} images. Everything passes. No annotations were changed.`,
    findings,
  };
}
