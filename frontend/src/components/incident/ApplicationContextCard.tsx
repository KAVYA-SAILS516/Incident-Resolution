import type { IncidentDetail } from "../../types";
import { Badge, Card, criticalityTone } from "../shared/common";

const RELATION: Record<string, string> = {
  dependency: "a dependency of this service",
  dependent: "calls this service",
  same_trace: "failed in the same requests",
};

/** Application context in plain language: what the service is, why it matters, what it depends on. */
export function ApplicationContextCard({ detail, onOpen }: { detail: IncidentDetail; onOpen?: (id: string) => void }) {
  const { incident: i, evidence } = detail;
  const ctx = evidence.application_context;
  if (!i.application && !ctx) return null;
  const traces = evidence.telemetry?.traces ?? [];
  const metrics = evidence.telemetry?.service_metrics;
  const links = i.correlated_incidents ?? [];

  return (
    <Card question="Why does it matter?" title="Application context">
      <dl className="facts compact">
        <div><dt>Application</dt><dd>{i.application ?? "—"}</dd></div>
        <div><dt>Observed with</dt><dd>OpenTelemetry (Logs · Metrics · Traces)</dd></div>
        <div><dt>Service</dt><dd>{i.service}</dd></div>
        <div><dt>Business criticality</dt>
          <dd>{i.service_criticality ? <Badge tone={criticalityTone(i.service_criticality)}>{i.service_criticality}</Badge> : <span className="muted">unknown</span>}</dd></div>
        <div><dt>What it does</dt><dd>{ctx?.purpose && ctx.purpose !== "unknown" ? ctx.purpose : <span className="muted">not documented</span>}</dd></div>
      </dl>
      {i.priority_reason && <p className="lead"><strong>{i.priority}:</strong> {i.priority_reason}</p>}
      {ctx && (
        <dl className="facts compact">
          <div><dt>Depends on</dt><dd>{ctx.depends_on.join(", ") || <span className="muted">nothing found</span>}</dd></div>
          <div><dt>Used by</dt><dd>{ctx.depended_on_by.join(", ") || <span className="muted">nothing found</span>}</dd></div>
        </dl>
      )}
      {links.length > 0 && (
        <>
          <span className="field-label">Also failing in the same requests</span>
          <ul className="plain">
            {links.map((l) => (
              <li key={l.incident_id}>
                <button type="button" className="link-button" onClick={() => onOpen?.(l.incident_id)}>{l.service}</button>{" "}
                <span className="muted small">— {RELATION[l.relation] ?? l.relation}; {l.shared_traces > 0 ? `${l.shared_traces} shared request(s)` : "failing at the same time (dependency)"}</span>
              </li>
            ))}
          </ul>
        </>
      )}
      <details>
        <summary>Telemetry evidence &amp; sources</summary>
        <p className="muted small">Sources: OpenTelemetry Logs (OpenSearch) · Prometheus Metrics · Jaeger Traces · Astronomy Shop configuration &amp; documentation · Gemini / Vertex AI.</p>
        <p className="small">{(i.trace_ids ?? []).length} request trace(s) linked to this incident; services involved: {(i.affected_services ?? [i.service]).join(", ")}.</p>
        {traces.map((t) => (
          <p key={t.trace_id} className="small">
            Trace {t.trace_id.slice(0, 8)}…: {t.span_count} steps across {t.services.join(", ")}
            {t.error_spans.length > 0 && <> — failed at {t.error_spans.map((s) => `${s.service ?? "?"} ${s.operation}`).join(", ")}</>}
          </p>
        ))}
        {metrics
          ? <p className="small">{i.service} over the last {Math.round(metrics.window_seconds / 60)} min: {metrics.requests} requests, {(metrics.error_ratio * 100).toFixed(1)}% failing.</p>
          : <p className="muted small">Live metrics and trace details are not available from the telemetry backend.</p>}
      </details>
    </Card>
  );
}
