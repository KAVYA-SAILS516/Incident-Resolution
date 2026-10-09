import type { IncidentDetail } from "../../types";
import { Badge, Card, InfoTip, riskTone } from "../shared/common";

/** Status text for the backend's own analysis.state - this page only ever reads it, never sets it. */
function statusMessage(detail: IncidentDetail): { working: string | null; idle: string } {
  const { state, error } = detail.incident.analysis;
  const investigated = !!detail.investigation;
  if (state === "recommending") {
    return { working: `The Resolution Decision Agent is preparing options${detail.evidence.runbook.available ? ` using runbook ${detail.evidence.runbook.runbook_id}` : ""}…`, idle: "" };
  }
  if (state === "failed") return { working: null, idle: `Automatic analysis failed: ${error}` };
  if (state === "disabled") return { working: null, idle: `Not analysed: ${error ?? "automatic analysis is disabled"}.` };
  if (investigated) return { working: null, idle: "Queued in the background workflow; recommendations not generated yet." };
  return { working: null, idle: "Waiting for the investigation to finish." };
}

export function RecommendationsCard({ detail, selectedIndex, onSelect }: {
  detail: IncidentDetail; selectedIndex: number; onSelect: (index: number) => void;
}) {
  const recs = detail.recommendations;
  const msg = statusMessage(detail);

  return (
    <Card
      question="What could be done?"
      title="Recommendations"
      actions={<InfoTip text="Resolution Decision Agent (Google ADK): runs as part of the background incident-resolution workflow, proposing actions from the investigation and preferring the simulated runbook. Nothing is executed." />}
    >
      {!recs && (
        msg.working ? (
          <div className="working" role="status">
            <span className="spinner" aria-hidden /> {msg.working}
          </div>
        ) : (
          <p className={detail.incident.analysis.state === "failed" ? "small" : "muted small"}>{msg.idle}</p>
        )
      )}
      {recs && (
        <>
          <div className="notice-exec">Recommendations only. Nothing below has been executed; a person decides in Decision &amp; safety.</div>
          <p className="muted small">
            {recs.model} · runbook {recs.runbook_id ?? "none"} (simulated) · {(recs.duration_ms / 1000).toFixed(1)}s ·{" "}
            {new Date(recs.created_at).toLocaleString()}
          </p>
          <p className="muted small">Click a recommendation to review it in Decision &amp; safety below.</p>
          <div className="option-list">
            {recs.recommendations.map((r, n) => {
              const selected = n === selectedIndex;
              return (
                <div key={n} className={`option-card${selected ? " option-selected" : ""}`} role="button" tabIndex={0}
                     aria-pressed={selected} onClick={() => onSelect(n)}
                     onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect(n); } }}>
                  <div className="option-head">
                    <h3>{n + 1}. {r.title}</h3>
                    {selected && <Badge tone="info">Selected</Badge>}
                  </div>
                  <div className="row option-facts">
                    <Badge tone={riskTone(r.risk)}>{r.risk} risk</Badge>
                    <Badge tone={r.source === "runbook" ? "info" : "neutral"}><span className="source-label">{r.source === "runbook" ? "RUNBOOK" : "AI-GENERATED"}</span></Badge>
                  </div>
                  <div className="option-text">
                    <p>{r.description}</p>
                    <p className="small"><strong>Why / evidence:</strong> {r.reason}</p>
                  </div>
                </div>
              );
            })}
          </div>
          {recs.validation_notes.length > 0 && (
            <details>
              <summary>Corrections applied by rules ({recs.validation_notes.length})</summary>
              <ul className="plain">{recs.validation_notes.map((note) => <li key={note} className="small">{note}</li>)}</ul>
            </details>
          )}
                  </>
      )}
    </Card>
  );
}
