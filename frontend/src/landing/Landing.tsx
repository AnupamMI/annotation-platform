import { useState } from "react";
import { NAME } from "../brand";
import { Icon } from "../components/Icon";
import { Mark } from "../components/Mark";
import { HERO_CELLS, HERO_H, HERO_W } from "./heroCells";
import "../styles/landing.css";

type Theme = "dark" | "light";

/** Same saved preference as the app, so the theme carries over when you sign in. */
function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(document.documentElement.dataset.theme === "light" ? "light" : "dark");
  const toggle = () => {
    const next: Theme = theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("ap-theme", next); } catch { /* private mode: theme just isn't remembered */ }
    setTheme(next);
  };
  return [theme, toggle];
}

const FEATURES = [
  { icon: "layers", title: "Your hierarchy, not ours",
    text: "Each project defines its own levels: patient → slide → scan for cytology, farm → field → flight for agriculture, store → aisle → shelf for retail. Progress, splits and analytics follow it." },
  { icon: "annotate", title: "People decide every label",
    text: "No model guesses labels for you to correct. Assist tools only run when an annotator asks, and the result is still theirs to accept." },
  { icon: "import", title: "Imports that settle themselves",
    text: "YOLO, COCO, CVAT XML and Label Studio files are matched to your images, label-mapped, validated and conflict-checked, then committed as one batch you can undo." },
  { icon: "ask", title: "Ask in plain language",
    text: "“How many slides are left this week?” becomes a read-only query you can see. Protocol questions are answered from your own guidelines, with the section cited." },
  { icon: "check", title: "Agents that ask first",
    text: "The dataset builder and consistency checker propose a plan. Nothing runs until someone approves it, and no agent can create, edit or delete an annotation." },
  { icon: "overview", title: "Honest analytics",
    text: "Time is active time, with idle detection, not how long a tab was open. It shows which images are hard, not who to rank." },
];

const STEPS = [
  { n: "1", title: "Upload", text: "Images or whole-slide files, filed into your hierarchy. Large images are tiled automatically." },
  { n: "2", title: "Annotate", text: "Boxes, polygons, points and brush in a fast deep-zoom viewer. Every edit is a new version; nothing is overwritten." },
  { n: "3", title: "Review", text: "Reviewers approve or send back with a note. Rule checks flag shapes outside their parent or labels that don't fit." },
  { n: "4", title: "Build datasets", text: "Split by patient, field or store so nothing leaks between train and test. Each dataset freezes the exact versions it used." },
];

const FORMATS = ["YOLO", "COCO", "CVAT XML", "Label Studio"];
const SLIDE_FORMATS = [".svs", ".ndpi", ".scn", ".bif"];

export default function Landing() {
  const [theme, toggleTheme] = useTheme();
  return (
    <div className="landing">
      <header className="l-header">
        <a className="l-brand" href="/">
          <Mark size={28} />
          <span>{NAME}</span>
        </a>
        <nav className="l-nav" aria-label="Page">
          <a href="#features">Features</a>
          <a href="#how">How it works</a>
          <a href="#privacy">Privacy</a>
        </nav>
        <div className="l-header-actions">
          <button className="btn ghost icon" onClick={toggleTheme} aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}>
            <Icon name={theme === "dark" ? "sun" : "moon"} size={16} />
          </button>
          <a className="btn primary" href="/app/">Sign in</a>
        </div>
      </header>

      <main>
        <section className="l-hero">
          <div className="l-hero-copy">
            <p className="l-eyebrow readout">Image annotation platform</p>
            <h1>People label.<br />AI handles the data.</h1>
            <p className="l-lede">
              {NAME} is for teams labeling images, from microscope slides to drone photos. Annotators make every
              decision; the platform tracks, imports, checks, queries and packages the work around them.
            </p>
            <div className="l-cta">
              <a className="btn primary lg" href="/app/">Open the app</a>
              <a className="btn lg" href="#how">See how it works</a>
            </div>
          </div>
          <HeroViewer />
        </section>

        <section id="features" className="l-section">
          <h2>What's different</h2>
          <div className="l-features">
            {FEATURES.map((f) => (
              <article key={f.title} className="l-card">
                <span className="l-card-icon"><Icon name={f.icon} size={18} /></span>
                <h3>{f.title}</h3>
                <p>{f.text}</p>
              </article>
            ))}
          </div>
        </section>

        <section id="how" className="l-section">
          <h2>How it works</h2>
          <ol className="l-steps">
            {STEPS.map((s) => (
              <li key={s.n}>
                <span className="l-step-n readout">{s.n}</span>
                <h3>{s.title}</h3>
                <p>{s.text}</p>
              </li>
            ))}
          </ol>
        </section>

        <section className="l-section l-split">
          <div>
            <h2>Whole-slide images, at full resolution</h2>
            <p className="l-body">
              Slides from Aperio, Hamamatsu, Leica and Ventana scanners open like any other image. The file is kept as it
              is and never loaded whole: overview tiles are made on upload, close-ups the first time someone zooms in.
              Scale bars and areas use the slide's own microns per pixel.
            </p>
            <p className="l-body">
              The photo of the glass label inside a slide file can show patient details, so it is never extracted or shown.
            </p>
            <div className="l-chips">
              {SLIDE_FORMATS.map((f) => <span key={f} className="l-chip readout">{f}</span>)}
            </div>
          </div>
          <dl className="l-stats">
            <div><dt>Test slide</dt><dd className="readout">46,000 × 32,893 px</dd></div>
            <div><dt>Overview ready after upload</dt><dd className="readout">0.7 s</dd></div>
            <div><dt>Close-up tile, first view</dt><dd className="readout">15–60 ms</dd></div>
            <div><dt>Peak memory for a 4.2 GB image</dt><dd className="readout">117 MB</dd></div>
          </dl>
        </section>

        <section className="l-section l-formats">
          <h2>Bring your labels, take them with you</h2>
          <p className="l-body">Import and export in the formats your tools already use. Every format is round-trip tested: export, import, identical annotations.</p>
          <div className="l-chips">
            {FORMATS.map((f) => <span key={f} className="l-chip">{f}</span>)}
          </div>
        </section>

        <section id="privacy" className="l-section">
          <h2>Built to be trusted with sensitive data</h2>
          <ul className="l-privacy">
            <li><strong>Pseudonymous by default.</strong> Work happens on IDs. Real identities sit in a separate restricted area only admins can reach.</li>
            <li><strong>Questions can only read.</strong> Ask runs one SELECT, in a read-only transaction, as a role that can see curated views and nothing else, with time and row limits.</li>
            <li><strong>AI never touches labels.</strong> Agents have no way to write annotations. That's enforced in code, not by instructions.</li>
            <li><strong>Everything is logged.</strong> Every question, query and agent step, with who approved what.</li>
          </ul>
        </section>

        <section className="l-section l-closing">
          <h2>Ready to look around?</h2>
          <p className="l-body">Sign in with your team account. The first visit to a new installation creates the admin account.</p>
          <a className="btn primary lg" href="/app/">Open {NAME}</a>
        </section>
      </main>

      <footer className="l-footer">
        <span className="l-brand sm"><Mark size={20} /> {NAME}</span>
        <span className="faint">Built with React, OpenSeadragon, FastAPI, PostgreSQL with PostGIS and pgvector.</span>
        <span className="faint">Private, in development.</span>
      </footer>
    </div>
  );
}

/** A real sample field of view with its real cell and nucleus outlines, drawn the way the viewer draws them. */
function HeroViewer() {
  const abnormal = HERO_CELLS.find((c) => c.abnormal);
  const box = abnormal && bbox(abnormal.cell);
  const umPerPx = 0.25;
  const barPx = 50 / umPerPx; // 50 µm
  return (
    <figure className="l-viewer" aria-label="Sample cytology image with cell and nucleus outlines">
      <div className="l-viewer-bar">
        <span className="readout">FOV-03</span>
        <span className="l-legend"><i style={{ background: "var(--green)" }} />Cell</span>
        <span className="l-legend"><i style={{ background: "var(--hema)" }} />Nucleus</span>
        <span className="l-legend"><i style={{ background: "var(--orange)" }} />Abnormal</span>
      </div>
      <div className="l-viewer-stage">
        <img src="/samples/fov_03.jpg" width={HERO_W} height={HERO_H} alt="" />
        <svg viewBox={`0 0 ${HERO_W} ${HERO_H}`} preserveAspectRatio="xMidYMid slice" aria-hidden>
          {HERO_CELLS.map((c, i) => (
            <g key={i}>
              <polygon points={c.cell} className={c.abnormal ? "l-ann abnormal" : "l-ann cell"} />
              <polygon points={c.nucleus} className="l-ann nucleus" />
            </g>
          ))}
          <g transform={`translate(${HERO_W - barPx - 48} ${HERO_H - 56})`}>
            <rect x="-14" y="-30" width={barPx + 28} height="60" rx="6" className="l-scale-bg" />
            <rect width={barPx} height="8" y="6" className="l-scale" />
            <text x={barPx / 2} y="-6" textAnchor="middle" className="l-scale-text">50 µm</text>
          </g>
        </svg>
        {box && (
          <span className="l-tag" style={{ right: `${(1 - box.x1 / HERO_W) * 100}%`, top: `${(box.y1 / HERO_H) * 100}%` }}>
            Abnormal · v3
          </span>
        )}
      </div>
      <figcaption className="l-viewer-foot readout">
        <span>1600 × 1200 px</span>
        <span>{umPerPx} µm/px</span>
        <span>{HERO_CELLS.length} cells</span>
      </figcaption>
    </figure>
  );
}

function bbox(points: string) {
  const xs: number[] = [];
  const ys: number[] = [];
  for (const p of points.split(" ")) {
    const [x, y] = p.split(",").map(Number);
    xs.push(x);
    ys.push(y);
  }
  return { x0: Math.min(...xs), y0: Math.min(...ys), x1: Math.max(...xs), y1: Math.max(...ys) };
}
