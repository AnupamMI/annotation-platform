/* Small stroke icon set (24px grid, 1.6 stroke). No icon library needed. */

const PATHS: Record<string, string> = {
  projects: "M4 5h7v6H4zM13 5h7v6h-7zM4 13h7v6H4zM13 13h7v6h-7z",
  overview: "M4 19V10M10 19V5M16 19v-7M21 19H3",
  data: "M3 7l9-4 9 4-9 4-9-4zM3 12l9 4 9-4M3 17l9 4 9-4",
  annotate: "M4 20l4-1 11-11-3-3L5 16l-1 4zM14 6l3 3",
  review: "M12 21a9 9 0 100-18 9 9 0 000 18zM8 12.5l2.5 2.5L16 9.5",
  import: "M12 3v12M7 10l5 5 5-5M4 17v3h16v-3",
  upload: "M12 15V3M7 8l5-5 5 5M4 17v3h16v-3",
  settings: "M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0M14 4v4M8 10v4M16 16v4",
  ask: "M4 5h16v11H9l-5 4V5zM8 9h8M8 12.5h5",
  sun: "M12 17a5 5 0 100-10 5 5 0 000 10zM12 1v2M12 21v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M1 12h2M21 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4",
  moon: "M20 14.5A8 8 0 019.5 4 8 8 0 1020 14.5z",
  select: "M5 3l13 7-6 2-2 6L5 3z",
  pan: "M8 13V5.5a1.5 1.5 0 013 0V12M11 11V4.5a1.5 1.5 0 013 0V12M14 11.5V6a1.5 1.5 0 013 0v8a7 7 0 01-7 7h-.5A6.5 6.5 0 015 17.5L3.5 14a1.5 1.5 0 012.6-1.5L8 15",
  box: "M4 4h16v16H4z",
  polygon: "M12 3l8 6-3 11H7L4 9l8-6z",
  point: "M12 15a3 3 0 100-6 3 3 0 000 6zM12 2v4M12 18v4M2 12h4M18 12h4",
  brush: "M9 15c-2 0-4 1.5-4 4 0 1 0 2-1 2 3 0 6-1 6-4M9.5 14.5L20 4l1 1-10.5 10.5",
  undo: "M9 14L4 9l5-5M4 9h11a5 5 0 010 10h-3",
  redo: "M15 14l5-5-5-5M20 9H9a5 5 0 000 10h3",
  fit: "M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5",
  eye: "M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12zM12 15a3 3 0 100-6 3 3 0 000 6z",
  eyeOff: "M3 3l18 18M10.6 5.1A10.9 10.9 0 0112 5c6 0 10 7 10 7a17 17 0 01-3.2 3.9M6.6 6.6A17.6 17.6 0 002 12s4 7 10 7a10 10 0 005.4-1.6M9.9 9.9a3 3 0 004.2 4.2",
  next: "M9 6l6 6-6 6",
  prev: "M15 6l-6 6 6 6",
  trash: "M4 7h16M10 11v6M14 11v6M5 7l1 13h12l1-13M9 7V4h6v3",
  history: "M3 12a9 9 0 103-6.7L3 8M3 3v5h5M12 7v5l3 3",
  close: "M6 6l12 12M18 6L6 18",
  chevronDown: "M6 9l6 6 6-6",
  chevronRight: "M9 6l6 6-6 6",
  plus: "M12 5v14M5 12h14",
  search: "M11 18a7 7 0 100-14 7 7 0 000 14zM20 20l-4-4",
  check: "M5 12.5l4.5 4.5L19 7.5",
  alert: "M12 4l9 16H3l9-16zM12 10v4M12 17.5v.5",
  keyboard: "M3 6h18v12H3zM7 10h0M11 10h0M15 10h0M7 14h10",
  send: "M4 12l16-8-6 16-2-7-8-1z",
  layers: "M12 3l9 5-9 5-9-5 9-5zM3 13l9 5 9-5",
  play: "M7 5v14l12-7L7 5z",
  download: "M12 4v11M7 10l5 5 5-5M5 20h14",
  file: "M6 3h8l5 5v13H6zM14 3v5h5",
  rollback: "M4 10h11a5 5 0 010 10H9M8 6l-4 4 4 4",
};

export function Icon({ name, size = 18, title }: { name: string; size?: number; title?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.6}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden={title ? undefined : true}
      role={title ? "img" : undefined}
    >
      {title && <title>{title}</title>}
      <path d={PATHS[name] ?? ""} />
    </svg>
  );
}
