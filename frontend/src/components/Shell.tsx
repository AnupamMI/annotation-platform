import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation, useParams } from "react-router-dom";
import { useStore } from "../store";
import { useNodePath } from "../hooks";
import { Icon } from "./Icon";
import { AskPanel } from "./AskPanel";
import { Toasts } from "./Toasts";
import { ChangePasswordDialog } from "./AuthScreens";
import { Mark } from "./Mark";

const PROJECT_NAV = [
  { to: "", label: "Overview", icon: "overview", end: true },
  { to: "/data", label: "Data", icon: "data" },
  { to: "/review", label: "Review", icon: "review" },
  { to: "/import", label: "Import", icon: "import" },
  { to: "/settings", label: "Setup", icon: "settings" },
];

export function Shell() {
  const { projectId } = useParams();
  const { theme, toggleTheme, setAskOpen, askOpen } = useStore();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k" && projectId) {
        e.preventDefault();
        setAskOpen(!useStore.getState().askOpen);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [projectId, setAskOpen]);

  return (
    <div className="shell">
      <nav className="rail" aria-label="Main">
        <Link to="/" className="rail-mark" aria-label="All projects">
          <Mark />
        </Link>
        <NavLink to="/" end className="rail-item">
          <Icon name="projects" />
          <span>Projects</span>
        </NavLink>
        {projectId && (
          <>
            <div className="rail-sep" />
            {PROJECT_NAV.map((n) => (
              <NavLink key={n.label} to={`/p/${projectId}${n.to}`} end={n.end} className="rail-item">
                <Icon name={n.icon} />
                <span>{n.label}</span>
              </NavLink>
            ))}
          </>
        )}
        <div className="rail-spacer" />
        <button className="rail-item" onClick={toggleTheme} aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}>
          <Icon name={theme === "dark" ? "sun" : "moon"} />
          <span>{theme === "dark" ? "Light" : "Dark"}</span>
        </button>
        <UserMenu />
      </nav>

      <div className="main">
        <TopBar onAsk={() => setAskOpen(!askOpen)} />
        <div className="content">
          <Outlet />
        </div>
      </div>

      {askOpen && projectId && <AskPanel projectId={projectId} onClose={() => setAskOpen(false)} />}
      <Toasts />
    </div>
  );
}

function TopBar({ onAsk }: { onAsk: () => void }) {
  const { projectId, itemId } = useParams();
  const location = useLocation();
  const project = useStore((s) => s.projects.find((p) => p.id === projectId));
  const item = useStore((s) => s.items.find((i) => i.id === itemId));
  const path = useNodePath(item?.nodeId ?? undefined);

  const page = location.pathname.split("/")[3];
  const pageName: Record<string, string> = { data: "Data", review: "Review", import: "Import", settings: "Setup" };

  return (
    <header className="topbar">
      <nav className="crumbs" aria-label="Breadcrumb">
        <Link to="/">Projects</Link>
        {project && (
          <>
            <Sep />
            <Link to={`/p/${project.id}`}>{project.name}</Link>
          </>
        )}
        {page && page !== "annotate" && pageName[page] && (
          <>
            <Sep />
            <span>{pageName[page]}</span>
          </>
        )}
        {item &&
          path.map((n) => (
            <span key={n.id} className="crumb-node">
              <Sep />
              <Link to={`/p/${projectId}/data?node=${n.id}`} className="readout">
                {n.externalId}
              </Link>
            </span>
          ))}
        {item && (
          <>
            <Sep />
            <span className="readout">{item.name}</span>
          </>
        )}
      </nav>
      <div className="topbar-right">
        <SaveState />
        {projectId && (
          <button className="btn sm" onClick={onAsk}>
            <Icon name="ask" size={15} />
            Ask
            <kbd>Ctrl K</kbd>
          </button>
        )}
      </div>
    </header>
  );
}

function Sep() {
  return <span className="crumb-sep" aria-hidden>/</span>;
}

/** Shows whether work is saved. In sample mode nothing is saved, and the flag says so. */
function SaveState() {
  const { source, sandbox, pendingWrites } = useStore();
  if (sandbox) {
    return (
      <span className="sample-flag" title="This demo runs in your browser on synthetic sample data. Nothing is sent or saved; reload to start fresh.">
        Demo: nothing is saved
      </span>
    );
  }
  if (source === "sample") {
    return (
      <span className="sample-flag" title="The backend isn't running, so the app shows synthetic sample data and keeps changes in memory only.">
        Sample data, not saved
      </span>
    );
  }
  return <span className={`save-state${pendingWrites ? " busy" : ""}`}>{pendingWrites ? "Saving" : "Saved"}</span>;
}

/** Until login exists: pick who you are acting as (useful to try the reviewer role). */
function UserMenu() {
  const { users, currentUserId, setCurrentUser, source, signOut } = useStore();
  const [open, setOpen] = useState(false);
  const [pw, setPw] = useState(false);
  const me = users.find((u) => u.id === currentUserId);
  const ROLE: Record<string, string> = { admin: "Admin", annotator: "Annotator", reviewer: "Reviewer", operator: "Scanner operator", viewer: "Viewer" };

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (!(e.target as Element).closest(".user-menu")) setOpen(false); };
    window.addEventListener("pointerdown", close);
    return () => window.removeEventListener("pointerdown", close);
  }, [open]);

  return (
    <div className="user-menu">
      <button className="rail-avatar" title={me ? `${me.displayName}, ${ROLE[me.role]}` : ""} onClick={() => setOpen((o) => !o)} aria-expanded={open} aria-label="Account">
        {me?.initials}
      </button>
      {open && source === "api" && (
        <div className="user-pop" role="menu">
          <div className="user-pop-me">
            <span className="mini-avatar">{me?.initials}</span>
            <span><strong>{me?.displayName}</strong><span className="faint"> {me ? ROLE[me.role] : ""}</span></span>
          </div>
          <button role="menuitem" onClick={() => { setPw(true); setOpen(false); }}>Change password</button>
          <button role="menuitem" onClick={() => { setOpen(false); signOut(); }}>Sign out</button>
        </div>
      )}
      {open && source === "sample" && (
        <div className="user-pop" role="menu">
          <p className="faint">Sample data: act as</p>
          {users.map((u) => (
            <button key={u.id} role="menuitemradio" aria-checked={u.id === currentUserId} className={u.id === currentUserId ? "on" : ""}
              onClick={() => { setCurrentUser(u.id); setOpen(false); }}>
              <span className="mini-avatar">{u.initials}</span>
              <span>{u.displayName}<span className="faint"> {ROLE[u.role]}</span></span>
            </button>
          ))}
        </div>
      )}
      {pw && <ChangePasswordDialog onClose={() => setPw(false)} />}
    </div>
  );
}
