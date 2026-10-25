import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { currentAnnotations, useStore } from "../store";
import { STATUS_LABEL, useCan, useProject } from "../hooks";
import { Icon } from "../components/Icon";
import { UploadDialog } from "../components/UploadDialog";
import { STATUS_ORDER } from "../components/Charts";
import type { ID, ItemStatus, Node } from "../types";

export function DataPage() {
  const project = useProject();
  const [params, setParams] = useSearchParams();
  const nodeFilter = params.get("node");
  const { items, nodes, users, annotations, setAssignee, toast, source } = useStore();
  const can = useCan();
  const [uploading, setUploading] = useState(params.get("upload") === "1");
  const [status, setStatus] = useState<ItemStatus | "all">("all");
  const [assignee, setAssigneeFilter] = useState<ID | "all">("all");
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState<Set<ID>>(new Set());
  const [assignTo, setAssignTo] = useState<ID>("");

  const pNodes = useMemo(() => nodes.filter((n) => n.projectId === project?.id), [nodes, project]);
  const its = useMemo(() => items.filter((i) => i.projectId === project?.id), [items, project]);

  const descendantLeafIds = (nodeId: ID): Set<ID> => {
    const out = new Set<ID>([nodeId]);
    let grew = true;
    while (grew) {
      grew = false;
      for (const n of pNodes) if (n.parentId && out.has(n.parentId) && !out.has(n.id)) { out.add(n.id); grew = true; }
    }
    return out;
  };

  const shown = useMemo(() => {
    const scope = nodeFilter && nodeFilter !== "unlinked" ? descendantLeafIds(nodeFilter) : null;
    return its.filter((i) =>
      (nodeFilter !== "unlinked" || i.nodeId === null) &&
      (!scope || (i.nodeId !== null && scope.has(i.nodeId))) &&
      (status === "all" || i.status === status) &&
      (assignee === "all" || i.assigneeId === assignee) &&
      (!query || i.name.toLowerCase().includes(query.toLowerCase())),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [its, nodeFilter, status, assignee, query, pNodes]);

  if (!project) return null;
  const roots = pNodes.filter((n) => !n.parentId);
  const level = (n: Node) => project.levels.find((l) => l.id === n.levelId)?.name ?? "";
  const countUnder = (n: Node) => { const s = descendantLeafIds(n.id); return its.filter((i) => i.nodeId !== null && s.has(i.nodeId)).length; };

  const pathTo = (nodeId: string | null): string[] => {
    const out: string[] = [];
    let cur = pNodes.find((n) => n.id === nodeId);
    while (cur) { out.unshift(cur.externalId); cur = pNodes.find((n) => n.id === cur!.parentId); }
    return out;
  };
  const toggle = (id: ID) => setPicked((p) => { const n = new Set(p); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const assign = () => {
    if (!assignTo) return;
    setAssignee([...picked], assignTo === "none" ? null : assignTo);
    toast(`${picked.size} ${picked.size === 1 ? "image" : "images"} assigned to ${assignTo === "none" ? "nobody" : users.find((u) => u.id === assignTo)?.displayName}.`);
    setPicked(new Set());
    setAssignTo("");
  };

  return (
    <div className="data-layout">
      <aside className="tree" aria-label="Hierarchy">
        <div className="tree-head"><h4>{project.levels.map((l) => l.name).join(" / ")}</h4></div>
        <button className={`tree-row${!nodeFilter ? " on" : ""}`} onClick={() => setParams({})}>
          <span>All images</span><span className="readout faint">{its.length}</span>
        </button>
        {roots.map((r) => (
          <TreeNode key={r.id} node={r} nodes={pNodes} depth={0} selected={nodeFilter} levelOf={level} countOf={countUnder}
            onPick={(id) => setParams({ node: id })} />
        ))}
        {its.some((i) => i.nodeId === null) && (
          <button className={`tree-row unlinked${nodeFilter === "unlinked" ? " on" : ""}`} onClick={() => setParams({ node: "unlinked" })}
            title="Images added by an import that aren't placed in the hierarchy yet">
            <span>Unlinked images</span><span className="readout faint">{its.filter((i) => i.nodeId === null).length}</span>
          </button>
        )}
      </aside>

      <section className="data-main">
        <div className="filters" role="search">
          <label className="search">
            <Icon name="search" size={15} />
            <input className="input" placeholder="Find an image" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Find an image" />
          </label>
          <select className="select" value={status} onChange={(e) => setStatus(e.target.value as ItemStatus | "all")} aria-label="Status">
            <option value="all">Any status</option>
            {STATUS_ORDER.map((s) => <option key={s} value={s}>{STATUS_LABEL[s]}</option>)}
          </select>
          <select className="select" value={assignee} onChange={(e) => setAssigneeFilter(e.target.value)} aria-label="Assignee">
            <option value="all">Anyone</option>
            {users.map((u) => <option key={u.id} value={u.id}>{u.displayName}</option>)}
          </select>
          <span className="dim filter-count"><span className="readout">{shown.length}</span> of {its.length}</span>
          {can.upload && <button className="btn primary" onClick={() => setUploading(true)} disabled={source !== "api"}
            title={source !== "api" ? "Start the backend to upload real images" : undefined}>
            <Icon name="upload" size={15} /> Upload images
          </button>}
        </div>
        {uploading && (
          <UploadDialog
            project={project}
            defaultPath={nodeFilter === "unlinked" ? [] : pathTo(nodeFilter)}
            onClose={() => { setUploading(false); if (params.get("upload")) setParams(nodeFilter ? { node: nodeFilter } : {}); }}
          />
        )}

        {picked.size > 0 && (
          <div className="bulk-bar">
            <span><span className="readout">{picked.size}</span> selected</span>
            <select className="select" value={assignTo} onChange={(e) => setAssignTo(e.target.value)} aria-label="Assign selected to">
              <option value="">Assign to…</option>
              {users.filter((u) => u.role !== "viewer").map((u) => <option key={u.id} value={u.id}>{u.displayName}</option>)}
              <option value="none">Nobody</option>
            </select>
            <button className="btn sm primary" onClick={assign} disabled={!assignTo}>Assign</button>
            <button className="btn sm ghost" onClick={() => setPicked(new Set())}>Clear</button>
          </div>
        )}

        {shown.length === 0 ? (
          <div className="empty panel">
            <h3>No images match these filters</h3>
            <button className="btn" onClick={() => { setStatus("all"); setAssigneeFilter("all"); setQuery(""); setParams({}); }}>Clear filters</button>
          </div>
        ) : (
          <ul className="thumb-grid">
            {shown.map((i) => {
              const n = currentAnnotations(annotations, i.id).length;
              const owner = users.find((u) => u.id === i.assigneeId);
              const slide = pNodes.find((x) => x.id === i.nodeId);
              return (
                <li key={i.id} className={`thumb${picked.has(i.id) ? " picked" : ""}`}>
                  <Link to={`/p/${project.id}/annotate/${i.id}`} className="thumb-img">
                    <img src={i.thumbUrl} alt={`${i.name} thumbnail`} loading="lazy" />
                  </Link>
                  {can.assign && <input type="checkbox" className="thumb-check" checked={picked.has(i.id)} onChange={() => toggle(i.id)} aria-label={`Select ${i.name}`} />}
                  <div className="thumb-meta">
                    <div className="thumb-line">
                      <Link to={`/p/${project.id}/annotate/${i.id}`} className="readout thumb-name">{i.name}</Link>
                      <span className="readout faint">{slide?.externalId}</span>
                    </div>
                    <div className="thumb-line">
                      <span className={`tag ${i.status}`}>{STATUS_LABEL[i.status]}</span>
                      <span className="faint"><span className="readout">{n}</span> shapes</span>
                      <span className="mini-avatar" title={owner?.displayName ?? "Unassigned"}>{owner?.initials ?? "–"}</span>
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}

function TreeNode({
  node, nodes, depth, selected, levelOf, countOf, onPick,
}: {
  node: Node; nodes: Node[]; depth: number; selected: string | null;
  levelOf: (n: Node) => string; countOf: (n: Node) => number; onPick: (id: ID) => void;
}) {
  const [open, setOpen] = useState(true);
  const kids = nodes.filter((n) => n.parentId === node.id);
  return (
    <>
      <div className={`tree-row${selected === node.id ? " on" : ""}`} style={{ paddingLeft: 10 + depth * 16 }}>
        {kids.length ? (
          <button className="tree-toggle" onClick={() => setOpen(!open)} aria-label={open ? "Collapse" : "Expand"} aria-expanded={open}>
            <Icon name={open ? "chevronDown" : "chevronRight"} size={14} />
          </button>
        ) : <span className="tree-toggle" />}
        <button className="tree-label" onClick={() => onPick(node.id)}>
          <span className="readout">{node.externalId}</span>
          <span className="faint">{levelOf(node).toLowerCase()}</span>
        </button>
        <span className="readout faint">{countOf(node)}</span>
      </div>
      {open && kids.map((k) => (
        <TreeNode key={k.id} node={k} nodes={nodes} depth={depth + 1} selected={selected} levelOf={levelOf} countOf={countOf} onPick={onPick} />
      ))}
    </>
  );
}
