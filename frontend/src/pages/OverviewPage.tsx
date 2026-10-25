import { useMemo } from "react";
import { Link, useNavigate } from "react-router-dom";
import { currentAnnotations, useStore } from "../store";
import { formatDuration, relativeTime, useProject, userName } from "../hooks";
import { DailyBars, StatusMeter, countStatuses } from "../components/Charts";
import { Icon } from "../components/Icon";

const DAY = 86_400_000;
const ACTION_TEXT: Record<string, string> = {
  "item.submitted": "submitted",
  "item.worked": "worked on",
  "review.approved": "approved",
  "review.rejected": "requested changes on",
  "import.committed": "imported",
  "dataset.created": "built dataset",
  "guideline.added": "added guideline",
  "guideline.removed": "removed guideline",
};

export function OverviewPage() {
  const project = useProject();
  const navigate = useNavigate();
  const { items, nodes, events, annotations, labels, users, currentUserId } = useStore();

  const its = useMemo(() => items.filter((i) => i.projectId === project?.id), [items, project]);
  const pEvents = useMemo(() => events.filter((e) => e.projectId === project?.id), [events, project]);

  const days = useMemo(() => {
    const today = new Date(); today.setHours(0, 0, 0, 0);
    return Array.from({ length: 14 }, (_, k) => {
      const date = new Date(today.getTime() - (13 - k) * DAY);
      const evs = pEvents.filter((e) => e.action === "item.submitted" && new Date(e.at).getTime() >= date.getTime() && new Date(e.at).getTime() < date.getTime() + DAY);
      return { date, value: evs.length, minutes: evs.reduce((t, e) => t + e.activeMs, 0) / 60000 };
    });
  }, [pEvents]);

  const people = useMemo(() => {
    const since = Date.now() - 14 * DAY;
    return users.map((u) => {
      const evs = pEvents.filter((e) => e.userId === u.id && new Date(e.at).getTime() >= since);
      const submitted = evs.filter((e) => e.action === "item.submitted");
      const reviewed = evs.filter((e) => e.action.startsWith("review."));
      const active = evs.reduce((t, e) => t + e.activeMs, 0);
      return { u, submitted: submitted.length, reviewed: reviewed.length, active, perImage: submitted.length ? submitted.reduce((t, e) => t + e.activeMs, 0) / submitted.length : 0 };
    }).filter((p) => p.submitted || p.reviewed);
  }, [users, pEvents]);

  const labelCounts = useMemo(() => {
    const pl = labels.filter((l) => l.projectId === project?.id);
    const all = its.flatMap((i) => currentAnnotations(annotations, i.id));
    return pl.map((l) => ({ l, n: all.filter((a) => a.labelId === l.id).length }));
  }, [labels, its, annotations, project]);

  if (!project) return <div className="empty"><h2>Project not found</h2><Link className="btn" to="/">All projects</Link></div>;

  const c = countStatuses(its);
  const slides = nodes.filter((n) => n.projectId === project.id && its.some((i) => i.nodeId === n.id));
  const nextMine = its.find((i) => i.assigneeId === currentUserId && ["new", "in_progress", "rejected"].includes(i.status)) ??
    its.find((i) => ["new", "in_progress", "rejected"].includes(i.status));
  const maxLabel = Math.max(1, ...labelCounts.map((x) => x.n));
  const activeTotal = days.reduce((t, d) => t + d.minutes, 0) * 60000;

  if (!its.length) {
    return (
      <div className="page">
        <div className="page-head"><div><h1>{project.name}</h1><p className="dim">{project.description}</p></div></div>
        <div className="panel empty">
          <Icon name="import" size={28} />
          <h3>No images in this project yet</h3>
          <p>Upload images to start annotating, or bring in a dataset someone already annotated.</p>
          <div className="drop-actions">
            <Link className="btn primary" to={`/p/${project.id}/data?upload=1`}>Upload images</Link>
            <Link className="btn" to={`/p/${project.id}/import`}>Import annotations</Link>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>{project.name}</h1>
          <p className="dim">{project.description}</p>
        </div>
        <div className="head-actions">
          {c.in_review > 0 && <Link className="btn" to={`/p/${project.id}/review`}>Review {c.in_review} {c.in_review === 1 ? "image" : "images"}</Link>}
          {nextMine && (
            <button className="btn primary" onClick={() => navigate(`/p/${project.id}/annotate/${nextMine.id}`)}>
              <Icon name="annotate" size={15} /> Continue annotating
            </button>
          )}
        </div>
      </div>

      <section className="panel progress-panel">
        <div className="progress-figures">
          <Figure value={`${Math.round((c.done / its.length) * 100)}%`} label={`${c.done} of ${its.length} images approved`} />
          <Figure value={String(c.in_review)} label="waiting for review" />
          <Figure value={String(c.new + c.in_progress + c.rejected)} label="still need annotation" />
          <Figure value={formatDuration(activeTotal)} label="active time, last 14 days" />
        </div>
        <StatusMeter items={its} height={10} legend />
      </section>

      <div className="overview-grid">
        <section className="panel">
          <div className="panel-head"><h3>Slides</h3><span className="dim">{slides.length} slides, split by patient</span></div>
          <table className="grid">
            <thead><tr><th>Patient</th><th>Slide</th><th className="num">Images</th><th>Progress</th><th>Stain</th></tr></thead>
            <tbody>
              {slides.map((s) => {
                const si = its.filter((i) => i.nodeId === s.id);
                const patient = nodes.find((n) => n.id === s.parentId);
                return (
                  <tr key={s.id} className="clickable" onClick={() => navigate(`/p/${project.id}/data?node=${s.id}`)}>
                    <td className="readout">{patient?.externalId}</td>
                    <td className="readout">{s.externalId}</td>
                    <td className="num readout">{si.length}</td>
                    <td className="meter-cell"><StatusMeter items={si} height={6} /></td>
                    <td className="dim">{s.meta.stain}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </section>

        <section className="panel">
          <div className="panel-head"><h3>Images submitted per day</h3><span className="dim">Last 14 days</span></div>
          <div className="panel-body"><DailyBars days={days} valueLabel="images submitted" /></div>
        </section>

        <section className="panel">
          <div className="panel-head"><h3>Team</h3><span className="dim">Active time only, last 14 days</span></div>
          <table className="grid">
            <thead><tr><th>Person</th><th className="num">Submitted</th><th className="num">Reviewed</th><th className="num">Active time</th><th className="num">Per image</th></tr></thead>
            <tbody>
              {people.map(({ u, submitted, reviewed, active, perImage }) => (
                <tr key={u.id}>
                  <td><span className="person"><span className="mini-avatar">{u.initials}</span>{u.displayName}</span></td>
                  <td className="num readout">{submitted || "–"}</td>
                  <td className="num readout">{reviewed || "–"}</td>
                  <td className="num readout">{formatDuration(active)}</td>
                  <td className="num readout">{perImage ? formatDuration(perImage) : "–"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        <section className="panel">
          <div className="panel-head"><h3>Annotations by label</h3><span className="dim">Current versions</span></div>
          <ul className="label-bars">
            {labelCounts.map(({ l, n }) => (
              <li key={l.id}>
                <span className="lb-name"><span className="swatch" style={{ background: l.color }} />{l.name}</span>
                <span className="lb-track"><span style={{ width: `${(n / maxLabel) * 100}%`, background: l.color }} /></span>
                <span className="readout lb-n">{n}</span>
              </li>
            ))}
          </ul>
        </section>

        <section className="panel span-2">
          <div className="panel-head"><h3>Recent activity</h3></div>
          <ul className="activity">
            {pEvents.slice(-8).reverse().map((e) => {
              const it = its.find((i) => i.id === e.itemId);
              return (
                <li key={e.id}>
                  <span className="mini-avatar">{users.find((u) => u.id === e.userId)?.initials}</span>
                  <span>
                    {userName(e.userId)} {ACTION_TEXT[e.action] ?? e.action}{" "}
                    {it ? <Link to={`/p/${project.id}/annotate/${it.id}`} className="readout">{it.name}</Link> : <span className="readout">{e.detail}</span>}
                    {e.activeMs > 0 && <span className="faint"> after {formatDuration(e.activeMs)} active</span>}
                  </span>
                  <span className="faint">{relativeTime(e.at)}</span>
                </li>
              );
            })}
          </ul>
        </section>
      </div>
    </div>
  );
}

function Figure({ value, label }: { value: string; label: string }) {
  return (
    <div className="figure">
      <span className="figure-value readout">{value}</span>
      <span className="dim">{label}</span>
    </div>
  );
}
