/** The instrument mark: a reticle over a slide. Strokes thin out slightly as it grows. */
export function Mark({ size = 26 }: { size?: number }) {
  const w = size > 32 ? 1.2 : 1.4;
  return (
    <svg width={size} height={size} viewBox="0 0 26 26" aria-hidden>
      <rect x="2" y="7" width="22" height="12" rx="1.5" fill="none" stroke="var(--line-strong)" strokeWidth={w} />
      <circle cx="13" cy="13" r="5.2" fill="none" stroke="var(--green)" strokeWidth={w + 0.2} />
      <path d="M13 4.5v4M13 17.5v4M4.5 13h4M17.5 13h4" stroke="var(--green)" strokeWidth={w + 0.2} strokeLinecap="round" />
    </svg>
  );
}
