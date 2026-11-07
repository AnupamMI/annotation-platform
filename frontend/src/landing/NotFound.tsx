import { NAME } from "../brand";
import { Mark } from "../components/Mark";
import "../styles/landing.css";

export default function NotFound() {
  return (
    <div className="boot l-404">
      <Mark size={48} />
      <h1>Nothing at this address</h1>
      <p className="dim">The page may have moved, or the link has a typo.</p>
      <div className="l-cta">
        <a className="btn primary" href="/">{NAME} home</a>
        <a className="btn" href="/demo/">Try the demo</a>
      </div>
    </div>
  );
}
