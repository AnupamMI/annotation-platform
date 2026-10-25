import { useMemo } from "react";
import { Link, useNavigate } from "react-router-dom";
import { currentAnnotations, useStore } from "../store";
import { relativeTime, useCan, useProject, userName } from "../hooks";
import { containmentProblems } from "../lib/geometry";
import { Icon } from "../components/Icon";

export function ReviewPage() {
  const project = useProject();
  const navigate = useNavigate();
  const { items, nodes, annotations, labels, events, reviews } = useStore();
  const can = useCan();
  const mode = can.review ? "?mode=review" : "";

  const rules = useMemo(
    () => new Map(labels.filter((l) => l.projectId === project?.id && l.mustBeInsideId).map((l) => [l.id, l.mustBeInsideId!])),
    [labels, project],
  );
  const queue = useMemo(
    () => items.filter((i) => i.projectId === project?.id && i.status === "in_review").map((i) => {
      const anns = currentAnnotations(annotations, i.id);
      const submitted = events.filter((e) => e.itemId === i.id && e.action === "item.submitted").at(-1);
      return { i, anns: anns.length, warnings: containmentProblems(anns, rules).length, submitted };
    }),
    [items, annotations, events, rules, project],
  );
  const changes = items.filter((i) => i.projectId === project?.id && i.status === "rejected");
  const decided = reviews.filter((r) => items.some((i) => i.id === r.itemId && i.projectId === project?.id)).sort((a, b) => b.at.localeCompare(a.at)).slice(0, 8);

  if (!project) return null;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Review</h1>
          <p className="dim">Approve finished images or send them back with a note. Annotations stay as the annotator drew them.</p>
        </div>
        {queue.length > 0 && can.review && (
          <button className="btn primary" onClick={() => navigate(`/p/${project.id}/annotate/${queue[0].i.id}?mode=review`)}>
            <Icon name="play" size={14} /> Start reviewing
          </button>
        )}
      </div>

      <section className="panel">
        <div className="panel-head"><h3>Waiting for review</h3><span className="readout dim">{queue.length}</span></div>
        {queue.length === 0 ? (
          <div className="empty"><Icon name="check" size={24} /><p>Nothing is waiting. Submitted images will appear here.</p></div>
        ) : (
          <table className="grid">
            <thead><tr><th>Image</th><th>Slide</th><th>Annotator</th><th className="num">Shapes</th><th>Rule checks</th><th>Submitted</th><th /></tr></thead>
            <tbody>
              {queue.map(({ i, anns, warnings, submitted }) => (
                <tr key={i.id} className="clickable" onClick={() => navigate(`/p/${project.id}/annotate/${i.id}${mode}`)}>
                  <td><span className="row-thumb"><img src={i.thumbUrl} alt="" /><span className="readout">{i.name}</span></span></td>
                  <td className="readout dim">{nodes.find((n) => n.id === i.nodeId)?.externalId}</td>
                  <td>{userName(i.assigneeId)}</td>
                  <td className="num readout">{anns}</td>
                  <td>{warnings ? <span className="warn-text"><Icon name="alert" size={14} /> {warnings} to check</span> : <span className="dim">All pass</span>}</td>
                  <td className="dim">{submitted ? relativeTime(submitted.at) : "earlier"}</td>
                  <td className="num"><span className="btn sm">{can.review ? "Review" : "Open"}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <div className="two-col">
        <section className="panel">
          <div className="panel-head"><h3>Changes requested</h3><span className="readout dim">{changes.length}</span></div>
          {changes.length === 0 ? <p className="panel-body dim">No images are waiting on fixes.</p> : (
            <ul className="activity">
              {changes.map((i) => {
                const r = reviews.filter((x) => x.itemId === i.id).sort((a, b) => b.at.localeCompare(a.at))[0];
                return (
                  <li key={i.id}>
                    <Link to={`/p/${project.id}/annotate/${i.id}`} className="readout">{i.name}</Link>
                    <span className="dim">{r?.comment || "No note"}</span>
                    <span className="faint">{userName(i.assigneeId)}</span>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
        <section className="panel">
          <div className="panel-head"><h3>Recent decisions</h3></div>
          <ul className="activity">
            {decided.map((r) => {
              const i = items.find((x) => x.id === r.itemId)!;
              return (
                <li key={r.id}>
                  <span className={`tag ${r.verdict === "approved" ? "done" : "rejected"}`}>{r.verdict === "approved" ? "Approved" : "Changes requested"}</span>
                  <span><Link to={`/p/${project.id}/annotate/${i.id}`} className="readout">{i.name}</Link> <span className="faint">by {userName(r.reviewerId)}</span></span>
                  <span className="faint">{relativeTime(r.at)}</span>
                </li>
              );
            })}
          </ul>
        </section>
      </div>
    </div>
  );
}
