import React, { lazy, Suspense } from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import "./styles/tokens.css";
import "./styles/base.css";

/*
  Entry points on one site:
  - /app/...   the annotation app (signed in). Lazy, so the landing page stays light.
  - /demo/...  the same app as a public sandbox: sample data in the browser, nothing saved.
  - anything else  the public landing page, which never calls the API.
*/
const App = lazy(() => import("./AppEntry"));
const Landing = lazy(() => import("./landing/Landing"));
const NotFound = lazy(() => import("./landing/NotFound"));

const path = window.location.pathname;
const base = ["/app", "/demo"].find((b) => path === b || path.startsWith(b + "/"));
if (base && path === base) window.history.replaceState(null, "", base + "/" + window.location.search);
const Page = base ? null : path === "/" || path === "/index.html" ? Landing : NotFound;

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <Suspense fallback={<div className="boot dim">Loading</div>}>
      {base ? (
        <BrowserRouter basename={base}>
          <App sandbox={base === "/demo"} />
        </BrowserRouter>
      ) : (
        Page && <Page />
      )}
    </Suspense>
  </React.StrictMode>,
);
