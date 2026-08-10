import { useState } from "react";
import { Navigate, NavLink, Route, Routes } from "react-router-dom";
import { useCurrentUser } from "./api/queries";
import type { GenerationJobDetail } from "./api/types";
import { AdminLayout, CatalogScreen, InvitesScreen } from "./features/admin";
import { LoginScreen } from "./features/auth";
import { ProjectBoard, ProjectListScreen } from "./features/director";
import { GenerateScreen } from "./features/generate";
import { JobModal, QueueSidebar } from "./features/queue";
import "./App.css";

function MainLayout() {
  const [selectedJobId, setSelectedJobId] = useState<number | null>(null);
  const [redoPayload, setRedoPayload] = useState<GenerationJobDetail | null>(null);

  function handleRedo(job: GenerationJobDetail) {
    setRedoPayload(job);
    setSelectedJobId(null);
  }

  return (
    <>
      <div className="app-layout">
        <GenerateScreen redoJob={redoPayload} onRedoConsumed={() => setRedoPayload(null)} />
        <QueueSidebar onOpenJob={setSelectedJobId} />
      </div>
      {selectedJobId != null && (
        <JobModal jobId={selectedJobId} onClose={() => setSelectedJobId(null)} onRedo={handleRedo} />
      )}
    </>
  );
}

function App() {
  const me = useCurrentUser();

  if (me.isLoading) {
    return (
      <section id="center">
        <p>Loading…</p>
      </section>
    );
  }

  if (me.isError) {
    return (
      <section id="center">
        <p className="error">Couldn't reach the server. Try reloading.</p>
      </section>
    );
  }

  if (!me.data?.authenticated) {
    return <LoginScreen />;
  }

  return (
    <>
      <nav className="app-nav">
        <span className="app-title">Minimax H3 Generator</span>
        <div className="app-nav-links">
          <NavLink to="/" end>
            Generate
          </NavLink>
          <NavLink to="/director">Director</NavLink>
          {me.data.is_staff && <NavLink to="/manage">Admin</NavLink>}
        </div>
        <span className="app-user">
          {me.data.username} · <a href="/accounts/logout/">Log out</a>
        </span>
      </nav>
      <main>
        <Routes>
          <Route path="/" element={<MainLayout />} />
          <Route path="/director" element={<ProjectListScreen />} />
          <Route path="/director/:projectId" element={<ProjectBoard />} />
          <Route
            path="/manage"
            element={me.data.is_staff ? <AdminLayout /> : <Navigate to="/" replace />}
          >
            <Route index element={<Navigate to="invites" replace />} />
            <Route path="invites" element={<InvitesScreen />} />
            <Route path="catalog" element={<CatalogScreen />} />
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </>
  );
}

export default App;
