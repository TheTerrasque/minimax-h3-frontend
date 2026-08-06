import { NavLink, Outlet } from "react-router-dom";

// Thin wrapper for everything under /manage -- a sub-nav (reusing the same
// .tab-strip/.tab styling as the Generate screen's content/mode tabs) plus
// whichever admin page is routed in below it. See App.tsx for the nested
// <Route path="/manage"> this wraps.
export function AdminLayout() {
  return (
    <section className="screen admin-screen">
      <nav className="tab-strip admin-tabs">
        <NavLink to="invites" className={({ isActive }) => `tab${isActive ? " selected" : ""}`}>
          Invites
        </NavLink>
        <NavLink to="catalog" className={({ isActive }) => `tab${isActive ? " selected" : ""}`}>
          Quality &amp; Duration
        </NavLink>
      </nav>
      <Outlet />
    </section>
  );
}
