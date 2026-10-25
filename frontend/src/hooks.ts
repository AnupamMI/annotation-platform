import { useParams } from "react-router-dom";
import { useMemo } from "react";
import { useStore } from "./store";
import type { ID, Node } from "./types";

export function useProject() {
  const { projectId } = useParams();
  const projects = useStore((s) => s.projects);
  return projects.find((p) => p.id === projectId);
}

/** Node chain from root to the given node, e.g. [patient, slide]. */
export function useNodePath(nodeId: ID | undefined): Node[] {
  const nodes = useStore((s) => s.nodes);
  return useMemo(() => {
    const out: Node[] = [];
    let cur = nodes.find((n) => n.id === nodeId);
    while (cur) {
      out.unshift(cur);
      cur = nodes.find((n) => n.id === cur!.parentId);
    }
    return out;
  }, [nodes, nodeId]);
}

export function userName(id: ID | null | undefined) {
  const u = useStore.getState().users.find((x) => x.id === id);
  return u?.displayName ?? "Unassigned";
}

/** Durations in reports: "2h 05m", "14m 22s", "45s". */
export function formatDuration(ms: number) {
  const s = Math.round(ms / 1000);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${h}h ${String(m).padStart(2, "0")}m`;
  if (m > 0) return `${m}m ${String(sec).padStart(2, "0")}s`;
  return `${sec}s`;
}

/** A running timer: "4:07" or "1:02:45". */
export function formatClock(ms: number) {
  const s = Math.floor(ms / 1000);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = String(s % 60).padStart(2, "0");
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${m}:${sec}`;
}

export function relativeTime(iso: string, now = Date.now()) {
  const diff = (now - new Date(iso).getTime()) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)} min ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} h ago`;
  const d = Math.floor(diff / 86400);
  return d === 1 ? "yesterday" : `${d} days ago`;
}

export const STATUS_LABEL: Record<string, string> = {
  new: "Not started",
  in_progress: "In progress",
  annotated: "Annotated",
  in_review: "In review",
  done: "Done",
  rejected: "Changes requested",
};

export type Role = "admin" | "annotator" | "reviewer" | "operator" | "viewer";

/** What the signed-in user's role allows. The server enforces the same rules; this only hides dead buttons. */
export function useCan() {
  const role = useStore((s) => s.users.find((u) => u.id === s.currentUserId)?.role) as Role | undefined;
  const is = (...roles: Role[]) => !!role && (role === "admin" || roles.includes(role));
  return {
    role,
    annotate: is("annotator", "reviewer"),
    review: is("reviewer"),
    assign: is("reviewer"),
    upload: is("operator"),
    manage: is(),
    exportData: is("reviewer"),
    importData: is(),
  };
}
