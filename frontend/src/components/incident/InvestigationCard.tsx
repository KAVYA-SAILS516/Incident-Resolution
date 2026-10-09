import type { IncidentDetail } from "../../types";
import { Badge, Card, Confidence, DataSourceList, InfoTip } from "../shared/common";

/** Status text for the backend's own analysis.state - this page only ever reads it, never sets it. */
function statusMessage(detail: IncidentDetail): { working: string | null; idle: string } {
  const { state, error } = detail.incident.analysis;
  if (state === "investigating") {
    return { working: `The Investigation Agent is analysing aggregated evidence and ${detail.evidence.data_limits.log_lines_shared_with_ai} representative log lines…`, idle: "" };
  }
  if (state === "queued") return { working: null, idle: "Queued in the background workflow; not analysed yet." };
  if (state === "failed") return { working: null, idle: `Automatic analysis failed: ${error}` };
  if (state === "disabled") return { working: null, idle: `Not analysed: ${error ?? "automatic analysis is disabled"}.` };
  return { working: null, idle: "Not analysed yet." };
}

export function InvestigationCard({ detail }: { detail: IncidentDetail }) {
  const inv = detail.investigation;
  const facts = inv?.evidence.filter((e) => e.kind === "FACT") ?? [];
  const inferences = inv?.evidence.filter((e) => e.kind === "AI_INFERENCE") ?? [];
  const stale = inv && inv.based_on_occurrences !== detail.incident.occurrences;
  const msg = statusMessage(detail);

  return (
    <Card
      question="Why did it happen?"
      title="Investigation & root cause"
      actions={<InfoTip text="Investigation Agent (Google ADK): runs as part of the background incident-resolution workflow, reads aggregated evidence through tools and proposes the most likely root cause. It separates log facts from AI inference." />}
    >
      {!inv && (
        msg.working ? (
          <div className="working" role="status">
            <span className="spinner" aria-hidden /> {msg.working}
          </div>
        ) : (
          <p className={detail.incident.analysis.state === "failed" ? "small" : "muted small"}>{msg.idle}</p>
        )
      )}
      {inv && (
        <>
          <p className="muted small">
            {inv.provider === "fake" ? "TEST PROVIDER (not Gemini)" : "Vertex AI"} · {inv.model} · tools used: {inv.tools_called.join(", ")} · {(inv.duration_ms / 1000).toFixed(1)}s ·{" "}
            {new Date(inv.created_at).toLocaleString()}
          </p>
          {stale && <div className="alert alert-warning">New log lines changed this incident since the investigation; the result may be out of date.</div>}
          {inv.insufficient_evidence && <div className="alert alert-warning"><strong>Insufficient evidence.</strong> The agent could not support a specific root cause from the available data.</div>}
          <div className="decision-grid">
            <div>
              <span className="field-label">Root cause hypothesis</span>
              <p className="lead"><strong>{inv.root_cause}</strong></p>
              <div className="row"><span className="muted">Confidence</span><Confidence value={inv.confidence} /></div>
            </div>
            <div>
              <span className="field-label">Evidence sources</span>
              <div className="row" style={{ flexWrap: "wrap" }}>
                <Badge tone="info">Logs · OpenTelemetry</Badge>
                {detail.evidence.telemetry?.service_metrics && <Badge tone="info">Metrics · Prometheus</Badge>}
                {(detail.evidence.telemetry?.traces.length ?? 0) > 0 && <Badge tone="info">Traces · Jaeger</Badge>}
                {detail.incident.application && <Badge tone="info">Application knowledge</Badge>}
                {detail.evidence.runbook.available && <Badge tone="info">Runbook {detail.evidence.runbook.runbook_id}</Badge>}
              </div>
              <p className="muted small">Deployment / change history is not connected; unavailable signals are listed under data limits.</p>
            </div>
          </div>
          <div className="three-col">
            <div className="panel panel-evidence">
              <div className="panel-title">1 · Facts <span className="muted">(from the logs)</span></div>
              <ul className="plain">
                {facts.map((e, n) => (
                  <li key={n}>{e.statement} {e.source && <span className="muted small">[{e.source}]</span>}</li>
                ))}
              </ul>
            </div>
            <div className="panel panel-analysis">
              <div className="panel-title">2 · AI inference <span className="muted">(interpretation)</span></div>
              <ul className="plain">{inferences.map((e, n) => <li key={n}>{e.statement}</li>)}</ul>
              <div className="panel-subtitle">Analysis</div>
              <p>{inv.analysis}</p>
            </div>
            <div className="panel panel-cause">
              <div className="panel-title">3 · Unknowns <span className="muted">(not in the data)</span></div>
              <ul className="plain">{inv.unknowns.map((u) => <li key={u} className="small">• {u}</li>)}</ul>
              <p className="muted small">The hypothesis is not confirmed until verified.</p>
              {inv.insufficient_evidence && <Badge tone="warning">Insufficient evidence</Badge>}
            </div>
          </div>
          <details>
            <summary>Data used and data limits</summary>
            <DataSourceList
              available={["Application logs (aggregated)", `${detail.evidence.data_limits.log_lines_shared_with_ai} representative log lines`]}
              missing={detail.evidence.data_limits.not_in_logs}
              notice="The agent cannot confirm a cause from data it does not have."
            />
          </details>
        </>
      )}
    </Card>
  );
}
