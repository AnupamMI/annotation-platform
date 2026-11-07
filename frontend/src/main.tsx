import React, { lazy, Suspense } from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import "./styles/tokens.css";
import "./styles/base.css";

/*
  Two entry points on one site:
  - /app/...  the annotation app (signed in). Lazy, so the landing page stays light.
  - anything else  the public landing page, which never calls the API.
*/
const App = lazy(() => import("./AppEntry"));
const Landing = lazy(() => import("./landing/Landing"));
const NotFound = lazy(() => import("./landing/NotFound"));

const path = window.location.pathname;
const inApp = path === "/app" || path.startsWith("/app/");
if (path === "/app") window.history.replaceState(null, "", "/app/" + window.location.search);
const Page = inApp ? null : path === "/" || path === "/index.html" ? Landing : NotFound;

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <Suspense fallback={<div className="boot dim">Loading</div>}>
      {inApp ? (
        <BrowserRouter basename="/app">
          <App />
        </BrowserRouter>
      ) : (
        Page && <Page />
      )}
    </Suspense>
  </React.StrictMode>,
);
