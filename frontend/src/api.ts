/*
  Thin client for the FastAPI backend. Vite proxies /api to http://localhost:8000.
  Shapes match src/types.ts (the backend returns camelCase).
*/

import type {
  ActivityEvent,
  AnnotationVersion,
  HierarchyLevel,
  ImportBatch,
  Item,
  LabelClass,
  Node,
  Project,
  Review,
  ShapeType,
  User,
} from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string, public body: unknown) {
    super(message);
  }
}

/** Called when the server says the session is gone (401), so the app can show sign-in. */
let onUnauthorized: (() => void) | null = null;
export const setUnauthorizedHandler = (fn: (() => void) | null) => {
  onUnauthorized = fn;
};

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(`/api${path}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  if (res.status === 401 && !path.startsWith("/auth/")) onUnauthorized?.();
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) throw new ApiError(res.status, messageFrom(data) ?? `Request failed (${res.status})`, data);
  return data as T;
}

function messageFrom(data: unknown): string | undefined {
  const d = (data as { detail?: unknown })?.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d) && d[0]?.msg) return String(d[0].msg);
  return undefined;
}

export interface Bundle {
  nodes: Node[];
  items: Item[];
  labels: LabelClass[];
  annotations: AnnotationVersion[];
  events: ActivityEvent[];
  reviews: Review[];
  imports: ImportBatch[];
}

export interface NewProjectInput {
  name: string;
  description?: string;
  domainPack: "generic" | "cytology";
  levels: { name: string; isSplitLevel: boolean }[];
  labels: { name: string; color: string; allowedShapes: ShapeType[]; mustBeInsideName?: string | null; hotkey?: string }[];
}

export interface UploadResult {
  created: Item[];
  nodes: Node[];
  skipped: { file: string; reason: string }[];
}

export const api = {
  async available(): Promise<boolean> {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 1500);
    try {
      const res = await fetch("/api/health", { signal: ctrl.signal });
      if (!res.ok) return false;
      const body = await res.json();
      return body?.database === "ok";
    } catch {
      return false;
    } finally {
      clearTimeout(t);
    }
  },
  users: () => req<User[]>("GET", "/users"),

  // ---------- sign-in ----------
  authStatus: () => req<{ setupNeeded: boolean; setupTokenRequired: boolean }>("GET", "/auth/status"),
  me: () => req<User>("GET", "/auth/me"),
  login: (email: string, password: string) => req<User>("POST", "/auth/login", { email, password }),
  setup: (email: string, displayName: string, password: string, setupToken?: string) =>
    req<User>("POST", "/auth/setup", { email, displayName, password, setupToken }),
  logout: () => req<void>("POST", "/auth/logout"),
  changePassword: (currentPassword: string, newPassword: string) => req<void>("POST", "/auth/password", { currentPassword, newPassword }),
  teamList: () => req<TeamMember[]>("GET", "/admin/users"),
  teamAdd: (m: { email: string; displayName: string; role: User["role"]; password: string }) => req<TeamMember>("POST", "/admin/users", m),
  teamUpdate: (id: string, patch: Partial<{ role: User["role"]; isActive: boolean; password: string; displayName: string }>) =>
    req<TeamMember>("PATCH", `/admin/users/${id}`, patch),
  projects: () => req<Project[]>("GET", "/projects"),
  bundle: (projectId: string) => req<Bundle>("GET", `/projects/${projectId}/bundle`),
  createProject: (body: NewProjectInput) => req<{ project: Project; labels: LabelClass[] }>("POST", "/projects", body),
  saveSetup: (projectId: string, levels: HierarchyLevel[], labels: LabelClass[]) =>
    req<{ project: Project; labels: LabelClass[] }>("PUT", `/projects/${projectId}/setup`, {
      levels: levels.map(({ id, name, isSplitLevel }) => ({ id, name, isSplitLevel })),
      labels: labels.map(({ id, name, color, allowedShapes, mustBeInsideId, hotkey }) => ({ id, name, color, allowedShapes, mustBeInsideId, hotkey })),
    }),
  itemAnnotations: (itemId: string) => req<AnnotationVersion[]>("GET", `/items/${itemId}/annotations`),
  appendVersion: (v: Omit<AnnotationVersion, "rowId" | "createdAt" | "createdBy" | "importBatchId">) =>
    req<AnnotationVersion>("POST", "/annotations/versions", {
      annotationId: v.annotationId, version: v.version, itemId: v.itemId, labelId: v.labelId, shape: v.shape,
      geometry: v.isDeleted ? undefined : v.geometry, isDeleted: v.isDeleted, source: v.source === "import" ? "human" : v.source,
    }),
  patchItem: (itemId: string, patch: Partial<Pick<Item, "status" | "assigneeId">>) => req<Item>("PATCH", `/items/${itemId}`, patch),
  assign: (itemIds: string[], assigneeId: string | null) => req<void>("POST", "/items/assign", { itemIds, assigneeId }),
  event: (e: { projectId: string; itemId: string | null; action: string; activeMs: number; detail?: string }) =>
    req<ActivityEvent>("POST", "/events", e),
  review: (r: { itemId: string; verdict: "approved" | "rejected"; comment: string }) => req<Review>("POST", "/reviews", r),
  rollbackImport: (batchId: string) => req<ImportBatch[]>("POST", `/imports/${batchId}/rollback`),

  /** One request per file so each can show progress and fail on its own. */
  upload(projectId: string, file: File, path: string[], onProgress?: (fraction: number) => void): Promise<UploadResult> {
    const form = new FormData();
    form.append("files", file);
    form.append("path", JSON.stringify(path));
    return sendForm<UploadResult>(`/projects/${projectId}/items`, form, onProgress);
  },

  // ---------- import and export ----------
  startImport(projectId: string, file: File, onProgress?: (fraction: number) => void): Promise<ImportPreview> {
    const form = new FormData();
    form.append("file", file);
    return sendForm<ImportPreview>(`/projects/${projectId}/imports`, form, onProgress);
  },
  getImport: (batchId: string) => req<ImportPreview>("GET", `/imports/${batchId}`),
  commitImport: (batchId: string, body: { labelMapping: Record<string, string>; conflictPolicy: ConflictPolicy; unlinked: "skip" | "hold" }) =>
    req<{ result: CommitResult; imports: ImportBatch[] }>("POST", `/imports/${batchId}/commit`, body),
  cancelImport: (batchId: string) => req<ImportBatch[]>("POST", `/imports/${batchId}/cancel`),

  // ask and agents
  aiStatus: () => req<AiStatus>("GET", "/ai/status"),
  ask: (projectId: string, question: string) => req<AskReply>("POST", `/projects/${projectId}/ask`, { question }),
  approveRun: (runId: string) => req<AgentRunOut>("POST", `/agent-runs/${runId}/approve`),
  cancelRun: (runId: string) => req<AgentRunOut>("POST", `/agent-runs/${runId}/cancel`),
  datasets: (projectId: string) => req<DatasetOut[]>("GET", `/projects/${projectId}/datasets`),
  documents: (projectId: string) => req<GuidelineDoc[]>("GET", `/projects/${projectId}/documents`),
  uploadDocument(projectId: string, file: File, title: string) {
    const form = new FormData();
    form.append("file", file);
    form.append("title", title);
    return sendForm<GuidelineDoc>(`/projects/${projectId}/documents`, form);
  },
  deleteDocument: (id: string) => req<void>("DELETE", `/documents/${id}`),

  /** Downloads a file from a GET endpoint through the browser's normal save flow. */
  async download(path: string) {
    const res = await fetch(path.startsWith("/api") ? path : `/api${path}`);
    if (!res.ok) {
      const data = await res.json().catch(() => null);
      throw new ApiError(res.status, messageFrom(data) ?? `Download failed (${res.status})`, data);
    }
    saveBlob(await res.blob(), /filename="([^"]+)"/.exec(res.headers.get("Content-Disposition") ?? "")?.[1] ?? "download.zip");
  },

  /** Downloads the export zip through the browser's normal save flow. */
  async exportProject(projectId: string, body: { format: ExportFormat; scope: "approved" | "annotated" | "all"; includeImages: boolean }) {
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    const res = await fetch(`/api/projects/${projectId}/export`, { method: "POST", headers, body: JSON.stringify(body) });
    if (!res.ok) {
      const data = await res.json().catch(() => null);
      throw new ApiError(res.status, messageFrom(data) ?? `Export failed (${res.status})`, data);
    }
    const blob = await res.blob();
    const name = /filename="([^"]+)"/.exec(res.headers.get("Content-Disposition") ?? "")?.[1] ?? "export.zip";
    saveBlob(blob, name);
    return { name, bytes: blob.size, notes: Number(res.headers.get("X-Export-Notes") ?? 0) };
  },
};

function saveBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

function sendForm<T>(path: string, form: FormData, onProgress?: (fraction: number) => void): Promise<T> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api${path}`);
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress?.(e.loaded / e.total);
    xhr.onload = () => {
      if (xhr.status === 401) onUnauthorized?.();
      let data: unknown = null;
      try { data = JSON.parse(xhr.responseText); } catch { /* not JSON */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data as T);
      else reject(new ApiError(xhr.status, messageFrom(data) ?? `Request failed (${xhr.status})`, data));
    };
    xhr.onerror = () => reject(new ApiError(0, "The server could not be reached.", null));
    xhr.send(form);
  });
}

export type ExportFormat = "yolo" | "coco" | "cvat_xml" | "label_studio";
export type ConflictPolicy = "keep_both" | "replace" | "skip";

export interface ImportReport {
  fileName: string;
  format: ExportFormat;
  formatName: string;
  images: { file: string; itemId: string | null; how: string; shapes: number; reason?: string }[];
  unmatched: { file: string; hasImage: boolean; reason: string; shapes: number }[];
  labels: { source: string; count: number; shapes: Record<string, number>; suggestedId: string | null; why: string }[];
  issues: { kind: string; level: "fixed" | "dropped" | "warn"; count: number; text: string }[];
  conflicts: { itemId: string; name: string; existing: number; incoming: number }[];
  totals: { images: number; matched: number; unmatched: number; unmatchedWithImage: number; annotations: number };
}

export interface ImportPreview {
  id: string;
  status: string;
  report: ImportReport;
}

export interface CommitResult {
  annotations: number;
  images: number;
  skippedImages: number;
  boxesAsPolygons: number;
}

export interface TeamMember extends User {
  email: string;
  isActive: boolean;
  canSignIn: boolean;
  lastLoginAt: string | null;
}

// ---------------- ask and agents ----------------
export interface AiStatus {
  provider: string;
  model: string | null;
  /** true when a language model is configured, so any question can be asked */
  freeForm: boolean;
}

export interface AskAnswer {
  kind: "answer";
  text: string;
  sql: string | null;
  table: { cols: string[]; rows: (string | number | null)[][]; truncated: boolean } | null;
  sources: { title: string; section: string; page: number | null; excerpt: string }[];
  links: { label: string; itemId: string }[];
  via: "rules" | "model";
}

export interface AgentRunOut {
  id: string;
  kind: "dataset_builder" | "consistency_checker";
  agent: string;
  status: "awaiting_approval" | "running" | "completed" | "cancelled" | "failed";
  request: string;
  summary: string;
  steps: { text: string; detail?: string }[];
  warning: string | null;
  result: { text?: string; findings?: { itemId: string; imageName: string; kind: string; note: string }[]; download?: string; notes?: unknown[] };
  error: string | null;
  toolCalls: { tool: string; ms?: number; summary?: string; error?: string }[];
  userId: string | null;
}

export type AskReply = AskAnswer | { kind: "plan"; run: AgentRunOut };

export interface DatasetOut {
  id: string;
  name: string;
  format: string;
  createdAt: string;
  images: number;
  splits: Record<"train" | "val" | "test", number>;
  download: string | null;
}

export interface GuidelineDoc {
  id: string;
  title: string;
  fileName: string;
  sections: number;
  createdAt: string;
}
