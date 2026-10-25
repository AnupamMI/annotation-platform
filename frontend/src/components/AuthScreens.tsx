import { useState, type FormEvent } from "react";
import { api } from "../api";
import { useStore } from "../store";

/** The reticle-over-slide mark, larger, for the sign-in screens. */
function Mark() {
  return (
    <svg width="44" height="44" viewBox="0 0 26 26" aria-hidden>
      <rect x="2" y="7" width="22" height="12" rx="1.5" fill="none" stroke="var(--line-strong)" strokeWidth="1.2" />
      <circle cx="13" cy="13" r="5.2" fill="none" stroke="var(--green)" strokeWidth="1.4" />
      <path d="M13 4.5v4M13 17.5v4M4.5 13h4M17.5 13h4" stroke="var(--green)" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  );
}

function Frame({ title, intro, children }: { title: string; intro: string; children: React.ReactNode }) {
  return (
    <div className="auth-stage">
      <div className="auth-card panel">
        <div className="auth-head">
          <Mark />
          <div>
            <h1>{title}</h1>
            <p className="dim">{intro}</p>
          </div>
        </div>
        {children}
      </div>
    </div>
  );
}

export function SignInScreen() {
  const { signIn, authNote } = useStore();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!email.trim() || !password) { setError("Enter your email and password."); return; }
    setBusy(true);
    setError("");
    try {
      await signIn(email.trim(), password);
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  };

  return (
    <Frame title="Sign in" intro="Annotation Platform">
      {authNote && <p className="auth-note">{authNote}</p>}
      <form className="form-stack" onSubmit={submit} noValidate>
        <label className="field">
          <span>Email</span>
          <input className="input" type="email" autoComplete="username" autoFocus value={email}
            onChange={(e) => { setEmail(e.target.value); setError(""); }} />
        </label>
        <label className="field">
          <span>Password</span>
          <input className="input" type="password" autoComplete="current-password" value={password}
            onChange={(e) => { setPassword(e.target.value); setError(""); }} />
        </label>
        {error && <p className="field-error" role="alert">{error}</p>}
        <button className="btn primary auth-submit" type="submit" disabled={busy}>{busy ? "Signing in" : "Sign in"}</button>
        <p className="faint auth-foot">No account yet? Ask an admin to add you. Forgotten password? An admin can set a new one.</p>
      </form>
    </Frame>
  );
}

export function SetupScreen() {
  const { createFirstAdmin } = useStore();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [again, setAgain] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!name.trim() || !email.trim()) { setError("Enter your name and email."); return; }
    if (password.length < 8) { setError("Use a password of at least 8 characters."); return; }
    if (password !== again) { setError("The two passwords don't match."); return; }
    setBusy(true);
    setError("");
    try {
      await createFirstAdmin(email.trim(), name.trim(), password);
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  };

  return (
    <Frame title="Create the admin account" intro="First start: nobody can sign in yet. This account can add the rest of the team.">
      <form className="form-stack" onSubmit={submit} noValidate>
        <label className="field"><span>Your name</span>
          <input className="input" autoComplete="name" autoFocus value={name} onChange={(e) => { setName(e.target.value); setError(""); }} />
        </label>
        <label className="field"><span>Email</span>
          <input className="input" type="email" autoComplete="username" value={email} onChange={(e) => { setEmail(e.target.value); setError(""); }} />
        </label>
        <label className="field"><span>Password</span>
          <input className="input" type="password" autoComplete="new-password" value={password} onChange={(e) => { setPassword(e.target.value); setError(""); }} />
          <em className="faint field-hint">At least 8 characters.</em>
        </label>
        <label className="field"><span>Password again</span>
          <input className="input" type="password" autoComplete="new-password" value={again} onChange={(e) => { setAgain(e.target.value); setError(""); }} />
        </label>
        {error && <p className="field-error" role="alert">{error}</p>}
        <button className="btn primary auth-submit" type="submit" disabled={busy}>{busy ? "Creating" : "Create account and sign in"}</button>
      </form>
    </Frame>
  );
}

export function ChangePasswordDialog({ onClose }: { onClose: () => void }) {
  const { toast } = useStore();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (next.length < 8) { setError("Use at least 8 characters."); return; }
    setBusy(true);
    try {
      await api.changePassword(current, next);
      toast("Password changed. Other devices were signed out.");
      onClose();
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  };

  return (
    <div className="scrim" onClick={onClose}>
      <form className="dialog" role="dialog" aria-label="Change password" onClick={(e) => e.stopPropagation()} onSubmit={submit} noValidate>
        <div className="panel-head"><h3>Change password</h3></div>
        <div className="panel-body form-stack">
          <label className="field"><span>Current password</span>
            <input className="input" type="password" autoComplete="current-password" autoFocus value={current} onChange={(e) => { setCurrent(e.target.value); setError(""); }} />
          </label>
          <label className="field"><span>New password</span>
            <input className="input" type="password" autoComplete="new-password" value={next} onChange={(e) => { setNext(e.target.value); setError(""); }} />
          </label>
          {error && <p className="field-error" role="alert">{error}</p>}
        </div>
        <div className="dialog-foot">
          <button type="button" className="btn ghost" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn primary" disabled={busy}>{busy ? "Saving" : "Change password"}</button>
        </div>
      </form>
    </div>
  );
}
