import { useState, type ReactNode } from "react";

export function InfoTip({ text, label = "What does this do?" }: { text: string; label?: string }) {
  const [open, setOpen] = useState(false);
  return (
    <span className="infotip">
      <button type="button" className="link-button" aria-expanded={open} onClick={() => setOpen(!open)}>
        ⓘ {label}
      </button>
      {open && <span className="infotip-text" role="note">{text}</span>}
    </span>
  );
}

export function Card({ title, question, children, className = "", actions }: {
  title?: string; question?: string; children: ReactNode; className?: string; actions?: ReactNode;
}) {
  return (
    <section className={`card ${className}`}>
      {(question || title) && (
        <header className="card-header">
          <div>
            {question && <div className="question">{question}</div>}
            {title && <h3>{title}</h3>}
          </div>
          {actions && <div className="card-actions">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

export function Badge({ children, tone = "neutral" }: { children: ReactNode; tone?: string }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

export function Confidence({ value }: { value: number | null | undefined }) {
  const percent = value == null ? 0 : Math.round(value * 100);
  return (
    <span className="confidence" title="Confidence">
      <span className="confidence-bar"><span style={{ width: `${percent}%` }} /></span>
      <span>{value == null ? "n/a" : `${percent}%`}</span>
    </span>
  );
}

export function DataSourceList({ available, missing, notice }: {
  available: string[]; missing: string[]; notice?: string;
}) {
  return (
    <div className="sources">
      <div>
        <div className="sources-title">Available</div>
        <ul className="plain">{available.map((s) => <li key={s} className="ok">✓ {s}</li>)}</ul>
      </div>
      <div>
        <div className="sources-title">Not available</div>
        <ul className="plain">{missing.map((s) => <li key={s} className="missing">✗ {s}</li>)}</ul>
        {missing.length > 0 && <p className="muted small">{notice ?? "These data sources were not available."}</p>}
      </div>
    </div>
  );
}

export const priorityTone = (p: string | null | undefined) =>
  p === "P1" ? "danger" : p === "P2" ? "warning" : p === "P3" ? "info" : "neutral";

export const riskTone = (r: string | undefined) =>
  r === "HIGH" ? "danger" : r === "MEDIUM" ? "warning" : r === "LOW" ? "success" : "neutral";

export const criticalityTone = (c: string | undefined) =>
  c === "CRITICAL" ? "danger" : c === "HIGH" ? "warning" : c === "MEDIUM" ? "info" : "neutral";

export const STATUS_LABEL: Record<string, string> = {
  open: "Open",
  investigated: "Investigated",
  recommendations_ready: "Recommendations ready",
};

export function statusTone(status: string) {
  if (status === "recommendations_ready") return "success";
  if (status === "investigated") return "info";
  return "neutral";
}
