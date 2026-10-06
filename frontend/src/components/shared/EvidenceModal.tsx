import { useEffect, useState } from "react";
import type { IncidentDetail, LogLine } from "../../types";
import { Badge } from "./common";

/** The representative log lines the agents see (a bounded, deterministic sample of the incident). */
export function EvidenceModal({ detail, onClose }: { detail: IncidentDetail; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const limits = detail.evidence.data_limits;
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" role="dialog" aria-modal="true" aria-label="Evidence" onClick={(e) => e.stopPropagation()}>
        <header className="modal-header">
          <h2>Evidence — {detail.incident.incident_id}</h2>
          <button type="button" onClick={onClose} aria-label="Close">✕</button>
        </header>
        <p className="muted">
          {limits.log_lines_shared_with_ai} representative lines of {limits.log_lines_in_incident} in this incident
          (first, last, slowest, one per host and per endpoint/status variant). These are the only raw lines the AI agents see.
        </p>
        <div className="table-wrap">
          <table className="table evidence">
            <thead><tr><th>Timestamp</th><th>Host</th><th>Severity</th><th>Request</th><th>Details</th></tr></thead>
            <tbody>{detail.evidence.representative_logs.map((e) => <EvidenceRow key={e.log_id} line={e} />)}</tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function EvidenceRow({ line }: { line: LogLine }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <tr>
        <td className="nowrap">{line.timestamp.replace("T", " ").replace("Z", "")}</td>
        <td>{line.host}</td>
        <td><Badge tone={line.level === "ERROR" || line.level === "CRITICAL" ? "danger" : "warning"}>{line.level}</Badge></td>
        <td className="mono small">{line.method} {line.endpoint} → {line.status_code} · {line.response_time_ms} ms</td>
        <td>
          <button type="button" className="link-button small" onClick={() => setOpen(!open)} aria-expanded={open}>
            {open ? "Hide" : "Show"} fields
          </button>
          <div className="muted small">{line.log_id}</div>
        </td>
      </tr>
      {open && (
        <tr className="raw-row">
          <td colSpan={5}>
            <pre className="raw">{JSON.stringify(line, null, 2)}</pre>
          </td>
        </tr>
      )}
    </>
  );
}
