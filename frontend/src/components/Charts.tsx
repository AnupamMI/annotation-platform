import { useState } from "react";
import type { Item, ItemStatus } from "../types";
import { STATUS_LABEL } from "../hooks";

export const STATUS_ORDER: ItemStatus[] = ["done", "in_review", "annotated", "in_progress", "rejected", "new"];
export const STATUS_FILL: Record<ItemStatus, string> = {
  done: "var(--chart-done)",
  in_review: "var(--chart-review)",
  annotated: "var(--chart-annotated)",
  in_progress: "var(--chart-progress)",
  rejected: "var(--chart-rejected)",
  new: "var(--chart-new)",
};

export function countStatuses(items: Item[]) {
  const c = Object.fromEntries(STATUS_ORDER.map((s) => [s, 0])) as Record<ItemStatus, number>;
  for (const i of items) c[i.status]++;
  return c;
}

/** Stacked proportion bar. Segment widths = share of images in each status; 2px gaps separate fills. */
export function StatusMeter({ items, height = 8, legend = false }: { items: Item[]; height?: number; legend?: boolean }) {
  const counts = countStatuses(items);
  const total = items.length || 1;
  const present = STATUS_ORDER.filter((s) => counts[s] > 0);
  return (
    <div className="status-meter">
      <div className="status-bar" style={{ height }} role="img" aria-label={present.map((s) => `${STATUS_LABEL[s]} ${counts[s]}`).join(", ")}>
        {present.map((s) => (
          <span key={s} style={{ flexGrow: counts[s] / total, background: STATUS_FILL[s] }} title={`${STATUS_LABEL[s]}: ${counts[s]} of ${items.length}`} />
        ))}
      </div>
      {legend && (
        <ul className="status-legend">
          {STATUS_ORDER.map((s) => (
            <li key={s} className={counts[s] ? "" : "zero"}>
              <span className="swatch" style={{ background: STATUS_FILL[s] }} />
              {STATUS_LABEL[s]}
              <span className="readout">{counts[s]}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Single-series bar chart: one bar per day. Hover shows the exact values. */
export function DailyBars({
  days, valueLabel, height = 140,
}: { days: { date: Date; value: number; minutes: number }[]; valueLabel: string; height?: number }) {
  const [hover, setHover] = useState<number | null>(null);
  const max = Math.max(1, ...days.map((d) => d.value));
  const niceMax = Math.ceil(max / 2) * 2;
  const W = 640, H = height, padL = 28, padB = 22, padT = 8;
  const plotW = W - padL - 4, plotH = H - padB - padT;
  const slot = plotW / days.length;
  const barW = Math.min(26, slot - 6);
  const y = (v: number) => padT + plotH - (v / niceMax) * plotH;
  const ticks = [0, niceMax / 2, niceMax];

  return (
    <div className="daily-bars">
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label={`${valueLabel} per day, last ${days.length} days`}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={padL} x2={W - 4} y1={y(t)} y2={y(t)} className="grid-line" />
            <text x={padL - 6} y={y(t) + 4} className="axis-text" textAnchor="end">{t}</text>
          </g>
        ))}
        {days.map((d, i) => {
          const x = padL + i * slot + (slot - barW) / 2;
          const top = y(d.value);
          const h = Math.max(0, padT + plotH - top);
          return (
            <g key={i} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
              <rect x={padL + i * slot} y={padT} width={slot} height={plotH} fill="transparent" />
              {h > 0 && (
                <path
                  d={roundedTop(x, top, barW, h, Math.min(4, h))}
                  className={`bar${hover === i ? " hot" : ""}`}
                />
              )}
              {(i % 2 === days.length % 2 || i === days.length - 1) && (
                <text x={x + barW / 2} y={H - 6} className="axis-text" textAnchor="middle">
                  {i === days.length - 1 ? "Today" : d.date.toLocaleDateString(undefined, { day: "numeric", month: "short" })}
                </text>
              )}
            </g>
          );
        })}
      </svg>
      {hover !== null && (
        <div className="chart-tip" style={{ left: `${((padL + hover * slot + slot / 2) / W) * 100}%` }}>
          <strong>{days[hover].date.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" })}</strong>
          <span><span className="readout">{days[hover].value}</span> {valueLabel}</span>
          <span><span className="readout">{Math.round(days[hover].minutes)}</span> active minutes</span>
        </div>
      )}
    </div>
  );
}

function roundedTop(x: number, y: number, w: number, h: number, r: number) {
  return `M${x} ${y + h}V${y + r}Q${x} ${y} ${x + r} ${y}H${x + w - r}Q${x + w} ${y} ${x + w} ${y + r}V${y + h}Z`;
}
