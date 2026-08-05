import { useEffect, useState } from "react";
import { apiFetch } from "./api/client";
import "./App.css";

// Placeholder shell -- see src/features/{auth,generate,queue}/ for where the
// real screens land (not built yet, see ARCHITECTURE.md "deferred to next
// pass"). Pinging /api/health/ here proves the SPA -> nginx -> Django path
// actually works end to end.
function App() {
  const [apiStatus, setApiStatus] = useState<"checking" | "ok" | "error">("checking");

  useEffect(() => {
    apiFetch<{ status: string }>("/health/")
      .then(() => setApiStatus("ok"))
      .catch(() => setApiStatus("error"));
  }, []);

  return (
    <section id="center">
      <h1>MinimaxH3 Front</h1>
      <p>Structure/architecture scaffold -- screens land in src/features/.</p>
      <p>
        Backend API: <strong>{apiStatus}</strong>
      </p>
    </section>
  );
}

export default App;
