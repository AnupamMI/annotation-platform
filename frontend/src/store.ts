/*
  App state.

  Two data sources:
  - "api":    the FastAPI backend is running. Everything is loaded from and saved to it.
  - "sample": the backend isn't reachable. Synthetic sample data, kept in memory only.

  Screens call the same actions either way. In api mode each write updates the
  screen at once and is sent to the server through one ordered queue, so versions
  arrive in order; if the server refuses a write, the screen is corrected.
*/

import { create } from "zustand";
import { api, ApiError, setUnauthorizedHandler, type Bundle, type NewProjectInput, type UploadResult } from "./api";
import { buildSampleData, newId, type SampleData, type SampleImage } from "./data/sample";
import type {
  ActivityEvent,
  AnnotationVersion,
  Geometry,
  HierarchyLevel,
  ID,
  ImportBatch,
  ItemStatus,
  LabelClass,
  Project,
  Review,
  ShapeType,
} from "./types";

export type Theme = "dark" | "light";
export type DataSource = "api" | "sample";
/** Sign-in state (api mode only): checking -> setup | signin -> in */
export type AuthState = "checking" | "setup" | "signin" | "in";

export interface Toast {
  id: number;
  text: string;
  tone?: "ok" | "warn" | "error";
}

interface State extends SampleData {
  source: DataSource;
  auth: AuthState;
  authNote: string | null; // e.g. "Your session ended"
  setupTokenRequired: boolean; // creating the first admin needs the server's SETUP_TOKEN
  sandbox: boolean; // the public demo: sample data by choice, not because the server is down
  ready: boolean;
  loadError: string | null;
  currentUserId: ID;
  theme: Theme;
  askOpen: boolean;
  toasts: Toast[];
  pendingWrites: number;

  /** sandbox: always sample data, even when the server is reachable (the public demo at /demo). */
  load: (sandbox?: boolean) => Promise<void>;
  signIn: (email: string, password: string) => Promise<void>;
  createFirstAdmin: (email: string, displayName: string, password: string, setupToken?: string) => Promise<void>;
  /** api mode: load everything again (e.g. after the demo project was rebuilt). */
  reload: () => Promise<void>;
  signOut: () => Promise<void>;
  refreshProject: (projectId: ID) => Promise<void>;
  setCurrentUser: (id: ID) => void;
  toggleTheme: () => void;
  setAskOpen: (open: boolean) => void;
  toast: (text: string, tone?: Toast["tone"]) => void;
  dismissToast: (id: number) => void;

  /** Appends a new version. Never edits an existing row. */
  appendVersion: (v: Omit<AnnotationVersion, "rowId" | "createdAt" | "createdBy"> & { createdBy?: ID }) => AnnotationVersion;
  createAnnotation: (itemId: ID, labelId: ID, shape: ShapeType, geometry: Geometry) => AnnotationVersion;
  updateAnnotation: (annotationId: ID, changes: { labelId?: ID; geometry?: Geometry }) => AnnotationVersion | null;
  deleteAnnotation: (annotationId: ID) => AnnotationVersion | null;
  restoreAnnotation: (annotationId: ID, from: AnnotationVersion) => AnnotationVersion;

  setItemStatus: (itemId: ID, status: ItemStatus) => void;
  logEvent: (e: Omit<ActivityEvent, "id" | "at" | "userId"> & { userId?: ID }) => void;
  addReview: (r: Omit<Review, "id" | "at" | "reviewerId">) => void;
  addImport: (b: ImportBatch) => void;
  setImportStatus: (id: ID, status: ImportBatch["status"]) => void;
  rollbackImport: (batch: ImportBatch) => Promise<void>;
  createProject: (input: NewProjectInput) => Promise<Project>;
  setAssignee: (itemIds: ID[], userId: ID | null) => void;
  saveSetup: (projectId: ID, levels: HierarchyLevel[], labels: LabelClass[]) => Promise<boolean>;
  uploadImages: (projectId: ID, file: File, path: string[], onProgress?: (f: number) => void) => Promise<UploadResult>;
}

const save = (key: string, value: string) => {
  try { localStorage.setItem(key, value); } catch { /* storage unavailable */ }
};

let toastSeq = 1;
let writeChain: Promise<unknown> = Promise.resolve();

const EMPTY: SampleData = {
  users: [], projects: [], nodes: [], items: [], labels: [], annotations: [], events: [], imports: [], reviews: [], cellsByItem: {},
};

export const useStore = create<State>((set, get) => {
  /** Runs server writes one after another, in the order they were made. */
  const enqueue = (label: string, write: () => Promise<void>) => {
    if (get().source !== "api") return;
    set((s) => ({ pendingWrites: s.pendingWrites + 1 }));
    writeChain = writeChain
      .then(write)
      .catch((err) => {
        const msg = err instanceof ApiError ? err.message : "The server could not be reached.";
        get().toast(`${label} was not saved: ${msg}`, "error");
      })
      .finally(() => set((s) => ({ pendingWrites: s.pendingWrites - 1 })));
  };

  const mergeBundle = (projectId: ID, b: Bundle) =>
    set((s) => {
      const itemIds = new Set(s.items.filter((i) => i.projectId === projectId).map((i) => i.id));
      return {
        nodes: [...s.nodes.filter((n) => n.projectId !== projectId), ...b.nodes],
        items: [...s.items.filter((i) => i.projectId !== projectId), ...b.items],
        labels: [...s.labels.filter((l) => l.projectId !== projectId), ...b.labels],
        annotations: [...s.annotations.filter((a) => !itemIds.has(a.itemId)), ...b.annotations],
        events: [...s.events.filter((e) => e.projectId !== projectId), ...b.events],
        reviews: [...s.reviews.filter((r) => !itemIds.has(r.itemId)), ...b.reviews],
        imports: [...s.imports.filter((x) => x.projectId !== projectId), ...b.imports],
      };
    });

  /** After sign-in: everything the signed-in user can see. */
  const loadData = async (userId: ID) => {
    const [users, projects] = await Promise.all([api.users(), api.projects()]);
    set({ ...EMPTY, users, projects, currentUserId: userId });
    const bundles = await Promise.all(projects.map((p) => api.bundle(p.id)));
    projects.forEach((p, i) => mergeBundle(p.id, bundles[i]));
    set({ auth: "in", authNote: null, ready: true });
  };

  const reloadItemAnnotations = async (itemId: ID) => {
    const rows = await api.itemAnnotations(itemId);
    set((s) => ({ annotations: [...s.annotations.filter((a) => a.itemId !== itemId), ...rows] }));
  };

  return {
    ...EMPTY,
    source: "sample",
    auth: "checking",
    authNote: null,
    setupTokenRequired: false,
    sandbox: false,
    ready: false,
    loadError: null,
    currentUserId: "u-anupam",
    theme: document.documentElement.dataset.theme === "light" ? "light" : "dark", // set in index.html: saved, else the system's
    askOpen: false,
    toasts: [],
    pendingWrites: 0,

    load: async (sandbox = false) => {
      if (get().ready) return;
      set({ sandbox });
      if (!sandbox && (await api.available())) {
        set({ source: "api" });
        setUnauthorizedHandler(() => {
          if (get().auth === "in") set({ ...EMPTY, auth: "signin", authNote: "Your session ended. Please sign in again." });
        });
        try {
          const { setupNeeded, setupTokenRequired } = await api.authStatus();
          if (setupNeeded) { set({ auth: "setup", setupTokenRequired, ready: true }); return; }
          let me;
          try { me = await api.me(); } catch { set({ auth: "signin", ready: true }); return; }
          await loadData(me.id);
        } catch (err) {
          set({ loadError: `The server answered but loading failed: ${(err as Error).message}` });
        }
        return;
      }
      try {
        const res = await fetch("/samples/samples.json");
        if (!res.ok) throw new Error(`samples.json returned ${res.status}`);
        const samples = (await res.json()) as SampleImage[];
        set({ ...buildSampleData(samples), source: "sample", auth: "in", ready: true });
      } catch (err) {
        set({ loadError: `The backend isn't running and the sample images could not be loaded (${(err as Error).message}).` });
      }
    },

    signIn: async (email, password) => {
      const me = await api.login(email, password);
      await loadData(me.id);
    },

    createFirstAdmin: async (email, displayName, password, setupToken) => {
      const me = await api.setup(email, displayName, password, setupToken);
      await loadData(me.id);
    },

    reload: async () => {
      if (get().source === "api") await loadData(get().currentUserId);
    },

    signOut: async () => {
      await writeChain.catch(() => undefined); // let pending saves finish first
      await api.logout().catch(() => undefined);
      set({ ...EMPTY, auth: "signin", authNote: null, askOpen: false });
    },

    refreshProject: async (projectId) => {
      if (get().source === "api") mergeBundle(projectId, await api.bundle(projectId));
    },

    /** Sample mode only: act as another sample user. With the API, you are who you signed in as. */
    setCurrentUser: (id) => {
      if (get().source === "api") return;
      save("ap-user", id);
      set({ currentUserId: id });
    },

    toggleTheme: () => {
      const theme: Theme = get().theme === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = theme;
      save("ap-theme", theme);
      set({ theme });
    },

    setAskOpen: (askOpen) => set({ askOpen }),

    toast: (text, tone = "ok") => {
      const id = toastSeq++;
      set((s) => ({ toasts: [...s.toasts, { id, text, tone }] }));
      setTimeout(() => get().dismissToast(id), tone === "error" ? 7000 : 4200);
    },
    dismissToast: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),

    appendVersion: (v) => {
      const row: AnnotationVersion = {
        ...v,
        createdBy: v.createdBy ?? get().currentUserId,
        rowId: newId("tmp"),
        createdAt: new Date().toISOString(),
      };
      set((s) => ({ annotations: [...s.annotations, row] }));
      enqueue("A change", async () => {
        try {
          const saved = await api.appendVersion(row);
          set((s) => ({ annotations: s.annotations.map((a) => (a.rowId === row.rowId ? saved : a)) }));
        } catch (err) {
          if (err instanceof ApiError && err.status === 409) {
            await reloadItemAnnotations(row.itemId);
            get().toast("Someone else changed this shape first. You're now seeing the latest version.", "warn");
            return;
          }
          set((s) => ({ annotations: s.annotations.filter((a) => a.rowId !== row.rowId) }));
          throw err;
        }
      });
      return row;
    },

    createAnnotation: (itemId, labelId, shape, geometry) =>
      get().appendVersion({
        annotationId: crypto.randomUUID(),
        version: 1,
        itemId,
        labelId,
        shape,
        geometry,
        isDeleted: false,
        source: "human",
      }),

    updateAnnotation: (annotationId, changes) => {
      const latest = latestVersion(get().annotations, annotationId);
      if (!latest || latest.isDeleted) return null;
      return get().appendVersion({ ...stripRow(latest), ...changes, version: latest.version + 1, source: "human", importBatchId: undefined });
    },

    deleteAnnotation: (annotationId) => {
      const latest = latestVersion(get().annotations, annotationId);
      if (!latest || latest.isDeleted) return null;
      return get().appendVersion({ ...stripRow(latest), version: latest.version + 1, isDeleted: true, source: "human", importBatchId: undefined });
    },

    restoreAnnotation: (annotationId, from) => {
      const latest = latestVersion(get().annotations, annotationId);
      return get().appendVersion({ ...stripRow(from), version: (latest?.version ?? 0) + 1, isDeleted: false, source: "human", importBatchId: undefined });
    },

    setItemStatus: (itemId, status) => {
      set((s) => ({ items: s.items.map((i) => (i.id === itemId ? { ...i, status } : i)) }));
      enqueue("The status change", async () => { await api.patchItem(itemId, { status }); });
    },

    logEvent: (e) => {
      const tempId = -Date.now() - Math.floor(Math.random() * 1000);
      set((s) => ({
        events: [...s.events, { ...e, userId: e.userId ?? s.currentUserId, id: s.source === "api" ? tempId : s.events.reduce((m, x) => Math.max(m, x.id), 0) + 1, at: new Date().toISOString() }],
      }));
      enqueue("Activity", async () => {
        const saved = await api.event({ projectId: e.projectId, itemId: e.itemId, action: e.action, activeMs: Math.round(e.activeMs), detail: e.detail });
        set((s) => ({ events: s.events.map((x) => (x.id === tempId ? saved : x)) }));
      });
    },

    addReview: (r) => {
      const tempId = newId("tmp");
      set((s) => ({ reviews: [...s.reviews, { ...r, id: tempId, reviewerId: s.currentUserId, at: new Date().toISOString() }] }));
      enqueue("The review", async () => {
        const saved = await api.review({ itemId: r.itemId, verdict: r.verdict, comment: r.comment });
        set((s) => ({ reviews: s.reviews.map((x) => (x.id === tempId ? saved : x)) }));
      });
    },

    addImport: (b) => set((s) => ({ imports: [b, ...s.imports] })),
    setImportStatus: (id, status) => set((s) => ({ imports: s.imports.map((b) => (b.id === id ? { ...b, status } : b)) })),

    rollbackImport: async (batch) => {
      if (get().source === "api") {
        await writeChain;
        await api.rollbackImport(batch.id);
        await get().refreshProject(batch.projectId);
        return;
      }
      const rows = get().annotations.filter((a) => a.importBatchId === batch.id);
      const ids = [...new Set(rows.map((r) => r.annotationId))];
      ids.forEach((id) => get().deleteAnnotation(id));
      new Set(rows.map((r) => r.itemId)).forEach((iid) => {
        const left = currentAnnotations(get().annotations, iid).length;
        get().setItemStatus(iid, left ? "in_progress" : "new");
      });
      get().setImportStatus(batch.id, "rolled_back");
    },

    createProject: async (input) => {
      if (get().source === "api") {
        const { project, labels } = await api.createProject(input);
        set((s) => ({ projects: [...s.projects, project], labels: [...s.labels, ...labels] }));
        return project;
      }
      const id = `p-${Date.now().toString(36)}`;
      const project: Project = {
        id, name: input.name, description: input.description ?? "", domainPack: input.domainPack, createdAt: new Date().toISOString(),
        levels: input.levels.map((l, i) => ({ id: `${id}-lv${i}`, depth: i, name: l.name, isSplitLevel: l.isSplitLevel })),
      };
      const labels: LabelClass[] = input.labels.map((l, i) => ({
        id: `${id}-lb${i}`, projectId: id, name: l.name, color: l.color, allowedShapes: l.allowedShapes, hotkey: String(i + 1),
        mustBeInsideId: l.mustBeInsideName ? `${id}-lb${input.labels.findIndex((x) => x.name === l.mustBeInsideName)}` : null,
      }));
      set((s) => ({ projects: [...s.projects, project], labels: [...s.labels, ...labels] }));
      return project;
    },

    setAssignee: (itemIds, userId) => {
      set((s) => ({ items: s.items.map((i) => (itemIds.includes(i.id) ? { ...i, assigneeId: userId } : i)) }));
      enqueue("The assignment", async () => { await api.assign(itemIds, userId); });
    },

    saveSetup: async (projectId, levels, labels) => {
      if (get().source === "api") {
        try {
          const out = await api.saveSetup(projectId, levels, labels);
          set((s) => ({
            projects: s.projects.map((p) => (p.id === projectId ? out.project : p)),
            labels: [...s.labels.filter((l) => l.projectId !== projectId), ...out.labels],
          }));
          return true;
        } catch (err) {
          get().toast((err as Error).message, "error");
          return false;
        }
      }
      set((s) => ({
        projects: s.projects.map((p) => (p.id === projectId ? { ...p, levels } : p)),
        labels: [...s.labels.filter((l) => l.projectId !== projectId), ...labels],
      }));
      return true;
    },

    uploadImages: async (projectId, file, path, onProgress) => {
      const out = await api.upload(projectId, file, path, onProgress);
      set((s) => ({
        items: [...s.items, ...out.created],
        nodes: [...s.nodes, ...out.nodes.filter((n) => !s.nodes.some((x) => x.id === n.id))],
      }));
      return out;
    },
  };
});

function stripRow(v: AnnotationVersion) {
  const { rowId: _r, createdAt: _c, createdBy: _b, ...rest } = v;
  return rest;
}

export function latestVersion(all: AnnotationVersion[], annotationId: ID): AnnotationVersion | undefined {
  let best: AnnotationVersion | undefined;
  for (const a of all) if (a.annotationId === annotationId && (!best || a.version > best.version)) best = a;
  return best;
}

/** Current state of an item: latest version of each annotation, deleted ones removed. */
export function currentAnnotations(all: AnnotationVersion[], itemId: ID): AnnotationVersion[] {
  const latest = new Map<ID, AnnotationVersion>();
  for (const a of all) {
    if (a.itemId !== itemId) continue;
    const prev = latest.get(a.annotationId);
    if (!prev || a.version > prev.version) latest.set(a.annotationId, a);
  }
  return [...latest.values()].filter((a) => !a.isDeleted);
}

export function historyOf(all: AnnotationVersion[], annotationId: ID): AnnotationVersion[] {
  return all.filter((a) => a.annotationId === annotationId).sort((a, b) => b.version - a.version);
}
