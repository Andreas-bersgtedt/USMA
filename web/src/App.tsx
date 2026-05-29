import { useEffect, useState } from "react";
import { Link, NavLink, Route, Routes } from "react-router-dom";
import { detectAvailableModules, detectMode, type ApiMode } from "./api/loader";
import { RunPicker } from "./components/RunPicker";
import { ThemePicker } from "./components/ThemePicker";
import CodeObjects from "./pages/CodeObjects";
import Configuration from "./pages/Configuration";
import Cost from "./pages/Cost";
import Dashboard from "./pages/Dashboard";
import Delta from "./pages/Delta";
import EstateOverview from "./pages/EstateOverview";
import Governance from "./pages/Governance";
import Help from "./pages/Help";
import PrintReport from "./pages/PrintReport";
import Recommendations from "./pages/Recommendations";
import Run from "./pages/Run";
import Runbook from "./pages/Runbook";
import RunDiff from "./pages/RunDiff";
import RunsHistory from "./pages/RunsHistory";
import Security from "./pages/Security";

type NavEntry = {
  to: string;
  label: string;
  end?: boolean;
  /**
   * Module slug that must have produced data for this tab to be useful.
   * When omitted the tab is always shown (e.g. Dashboard, Help, control-
   * plane operational tabs like Run/Runs/Configuration).
   */
  requires?: string;
  /**
   * Any-of variant: tab is visible if at least one of the listed module
   * slugs is available. Used for tabs that aggregate multiple modules
   * (e.g. SQL Surface = dedicated pool code objects + serverless top
   * queries).
   */
  requiresAny?: readonly string[];
  /**
   * When true, this tab is shown even when no run data exists yet
   * (control-plane: empty availability set). Used to keep the navigation
   * minimal on a fresh install — only entry points to produce a run and
   * documentation should be visible.
   */
  alwaysShow?: boolean;
};

const STATIC_NAV: NavEntry[] = [
  { to: "/", label: "Dashboard", end: true },
  {
    to: "/code-objects",
    label: "SQL Surface",
    requiresAny: ["dedicated_pools", "serverless_pools"],
  },
  { to: "/recommendations", label: "Recommendations", requires: "fabric_mapping" },
  { to: "/runbook", label: "Runbook", requires: "fabric_mapping" },
  { to: "/delta", label: "Delta", requires: "run_delta" },
  { to: "/cost", label: "Cost", requires: "cost" },
  { to: "/governance", label: "Governance", requires: "governance" },
  { to: "/security", label: "Security", requires: "security" },
  { to: "/help", label: "Help" },
];

const CONTROL_PLANE_NAV: NavEntry[] = [
  { to: "/", label: "Overview", end: true },
  { to: "/dashboard", label: "Dashboard" },
  {
    to: "/code-objects",
    label: "SQL Surface",
    requiresAny: ["dedicated_pools", "serverless_pools"],
  },
  { to: "/recommendations", label: "Recommendations", requires: "fabric_mapping" },
  { to: "/runbook", label: "Runbook", requires: "fabric_mapping" },
  { to: "/cost", label: "Cost", requires: "cost" },
  { to: "/governance", label: "Governance", requires: "governance" },
  { to: "/security", label: "Security", requires: "security" },
  { to: "/run", label: "Run", alwaysShow: true },
  { to: "/runs", label: "Runs" },
  { to: "/diff", label: "Diff" },
  { to: "/configuration", label: "Configuration", alwaysShow: true },
  { to: "/help", label: "Help", alwaysShow: true },
];

export default function App() {
  const [mode, setMode] = useState<ApiMode | null>(null);
  const [available, setAvailable] = useState<Set<string> | null>(null);

  useEffect(() => {
    detectMode().then(setMode).catch(() => setMode("static"));
    detectAvailableModules()
      .then(setAvailable)
      .catch(() => setAvailable(new Set()));
  }, []);

  const baseNav = mode === "control-plane" ? CONTROL_PLANE_NAV : STATIC_NAV;
  // Until the availability probe finishes, render the full nav so the
  // first paint doesn't flash a stripped-down menu. Once we know which
  // modules produced data:
  //   - If no run data exists yet (control-plane, empty set), show only
  //     entry points (Run, Configuration, Help) to keep navigation clean.
  //   - Otherwise hide tabs whose required module is absent.
  const nav =
    available === null
      ? baseNav
      : mode === "control-plane" && available.size === 0
        ? baseNav.filter((n) => n.alwaysShow)
        : baseNav.filter((n) => {
            if (n.requires && !available.has(n.requires)) return false;
            if (n.requiresAny && !n.requiresAny.some((m) => available.has(m))) return false;
            return true;
          });

  return (
    <div className="layout">
      <header className="topbar">
        <Link to="/" className="brand">
          Unified Solution Migration Analyzer
        </Link>
        <nav className="nav">
          {nav.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.end}
              className={({ isActive }) => (isActive ? "active" : "")}
            >
              {n.label}
            </NavLink>
          ))}
        </nav>
        {mode === "control-plane" && (
          <div className="picker">
            <RunPicker />
          </div>
        )}
        <ThemePicker />
      </header>
      <main className="content">
        <Routes>
          {mode === "control-plane" ? (
            <>
              <Route path="/" element={<EstateOverview />} />
              <Route path="/dashboard" element={<Dashboard />} />
            </>
          ) : (
            <Route path="/" element={<Dashboard />} />
          )}
          <Route path="/code-objects" element={<CodeObjects />} />
          <Route path="/recommendations" element={<Recommendations />} />
          <Route path="/runbook" element={<Runbook />} />
          <Route path="/delta" element={<Delta />} />
          <Route path="/cost" element={<Cost />} />
          <Route path="/governance" element={<Governance />} />
          <Route path="/security" element={<Security />} />
          <Route path="/help" element={<Help />} />
          <Route path="/help/:slug" element={<Help />} />
          {mode === "control-plane" && (
            <>
              <Route path="/run" element={<Run />} />
              <Route path="/runs" element={<RunsHistory />} />
              <Route path="/diff" element={<RunDiff />} />
              <Route path="/configuration" element={<Configuration />} />
              <Route path="/report/print" element={<PrintReport />} />
            </>
          )}
        </Routes>
      </main>
      <footer className="footer">
        <span>
          {mode === "control-plane"
            ? "Control plane — local FastAPI backend. No data leaves your machine."
            : "Static SPA — reads JSON outputs in place. No data leaves your machine."}
        </span>
      </footer>
    </div>
  );
}

