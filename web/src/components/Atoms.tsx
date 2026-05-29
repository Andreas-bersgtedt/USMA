import type { ReactNode } from "react";
import { severityLabel } from "../lib/labels";

export function StatCard(props: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
}) {
  return (
    <div className="card">
      <div className="label">{props.label}</div>
      <div className="value">{props.value}</div>
      {props.sub && <div className="sub">{props.sub}</div>}
    </div>
  );
}

export function SeverityPill({ severity }: { severity: string }) {
  const cls =
    severity === "blocker" || severity === "incompatible" ? "err"
    : severity === "warning" || severity === "needs_review" ? "warn"
    : severity === "info" || severity === "compatible" ? "ok"
    : "muted";
  return <span className={`pill ${cls}`} title={severity}>{severityLabel(severity)}</span>;
}

export function ScorePill({ score }: { score: number }) {
  const cls = score >= 80 ? "ok" : score >= 50 ? "warn" : "err";
  return <span className={`pill ${cls}`}>{score}</span>;
}

export function PctPill({ pct }: { pct: number | null | undefined }) {
  if (pct == null) return <span className="muted">—</span>;
  const cls = pct >= 80 ? "ok" : pct >= 50 ? "warn" : "err";
  return <span className={`pill ${cls}`}>{pct}%</span>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}
