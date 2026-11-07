/*
  Frontend types. These mirror the backend tables (backend/app/models) so the
  sample-data layer can be swapped for the real API without touching the screens.
*/

export type ID = string;

export type Role = "admin" | "annotator" | "reviewer" | "operator" | "viewer";

export interface User {
  id: ID;
  displayName: string;
  initials: string;
  role: Role;
}

export interface HierarchyLevel {
  id: ID;
  depth: number;
  name: string; // "patient", "slide", "FOV" ...
  isSplitLevel: boolean;
}

export interface Project {
  id: ID;
  name: string;
  description: string;
  domainPack: "generic" | "cytology";
  levels: HierarchyLevel[];
  createdAt: string;
}

export interface Node {
  id: ID;
  projectId: ID;
  levelId: ID;
  parentId: ID | null;
  externalId: string; // pseudonymous, e.g. "P-0101"
  meta: Record<string, string>;
}

export type ItemStatus = "new" | "in_progress" | "annotated" | "in_review" | "done" | "rejected";

export interface Item {
  id: ID;
  projectId: ID;
  nodeId: ID | null; // the leaf node (e.g. a slide) this image belongs to
  name: string; // display name, e.g. "FOV 03"
  url: string;
  thumbUrl: string;
  dziUrl?: string | null; // Deep Zoom tiles for large images
  mpp?: number | null; // microns per pixel from the scanner, when known
  width: number;
  height: number;
  status: ItemStatus;
  assigneeId: ID | null;
  sha256: string;
}

export type ShapeType = "bbox" | "polygon" | "point";

export interface LabelClass {
  id: ID;
  projectId: ID;
  name: string;
  color: string;
  allowedShapes: ShapeType[];
  mustBeInsideId: ID | null;
  hotkey: string; // "1".."9"
}

/** Polygon rings as [x, y] pairs in full-image pixels. MultiPolygon = Polygon[]; Polygon = Ring[]. */
export type Ring = [number, number][];
export type MultiPolygon = Ring[][];

export type Geometry =
  | { kind: "bbox"; x: number; y: number; w: number; h: number }
  | { kind: "region"; polygons: MultiPolygon }
  | { kind: "point"; x: number; y: number };

export type AnnotationSource = "human" | "tool_assisted" | "import";

/** One VERSION of an annotation. Rows are never edited; changes append a new version. */
export interface AnnotationVersion {
  rowId: ID;
  annotationId: ID;
  version: number;
  itemId: ID;
  labelId: ID;
  shape: ShapeType;
  geometry: Geometry;
  isDeleted: boolean;
  source: AnnotationSource;
  createdBy: ID;
  createdAt: string;
  importBatchId?: ID;
}

export interface ActivityEvent {
  id: number;
  projectId: ID;
  userId: ID;
  itemId: ID | null;
  action: string; // "annotation.created", "item.submitted", "review.approved" ...
  activeMs: number;
  at: string;
  detail?: string;
}

export type ImportStatus = "awaiting_approval" | "committed" | "rolled_back" | "failed";

export interface ImportBatch {
  id: ID;
  projectId: ID;
  fileName: string;
  format: "yolo" | "coco" | "cvat_xml" | "label_studio";
  status: ImportStatus;
  uploadedBy: ID;
  createdAt: string;
  annotations: number;
  images: number;
}

export interface Review {
  id: ID;
  itemId: ID;
  reviewerId: ID;
  verdict: "approved" | "rejected";
  comment: string;
  at: string;
}
