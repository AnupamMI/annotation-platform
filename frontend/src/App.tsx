import { useEffect } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { useStore } from "./store";
import { Shell } from "./components/Shell";
import { SetupScreen, SignInScreen } from "./components/AuthScreens";
import { ProjectsPage } from "./pages/ProjectsPage";
import { OverviewPage } from "./pages/OverviewPage";
import { DataPage } from "./pages/DataPage";
import { WorkspacePage } from "./pages/WorkspacePage";
import { ReviewPage } from "./pages/ReviewPage";
import { ImportPage } from "./pages/ImportPage";
import { SettingsPage } from "./pages/SettingsPage";

export default function App() {
  const { ready, loadError, load, auth } = useStore();

  useEffect(() => {
    load();
  }, [load]);

  if (loadError) {
    return (
      <div className="boot">
        <h2>Sample data is missing</h2>
        <p className="dim">{loadError}</p>
      </div>
    );
  }
  if (auth === "setup") return <SetupScreen />;
  if (auth === "signin") return <SignInScreen />;
  if (!ready || auth !== "in") return <div className="boot dim">Loading</div>;

  return (
    <Routes>
      <Route element={<Shell />}>
        <Route path="/" element={<ProjectsPage />} />
        <Route path="/p/:projectId" element={<OverviewPage />} />
        <Route path="/p/:projectId/data" element={<DataPage />} />
        <Route path="/p/:projectId/annotate/:itemId" element={<WorkspacePage />} />
        <Route path="/p/:projectId/review" element={<ReviewPage />} />
        <Route path="/p/:projectId/import" element={<ImportPage />} />
        <Route path="/p/:projectId/settings" element={<SettingsPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
