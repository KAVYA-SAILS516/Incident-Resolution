import type { IncidentDetail } from "../../types";
import { Badge, Card, criticalityTone, InfoTip, priorityTone } from "../shared/common";

/** Evidence and triage details: deterministic facts, priority factors and related errors (no AI). */
export function IncidentOverview({ detail }: { detail: IncidentDetail }) {
  const { incident: i, evidence } = detail;
  const spike = evidence.related_errors.cross_service_spike;
  return (
    <>
      <Card
        question="What does the evidence show?"
        title="Evidence & triage details"
        actions={<InfoTip text="Computed by deterministic rules from the logs. The Investigation Agent receives these facts through its tools." />}
      >
        <ul className="plain">{evidence.facts.map((f) => <li key={f}>• {f}</li>)}</ul>
        <p className="small">
          {spike.spike_minutes_in_window.length
            ? <Badge tone="warning">Overlaps a cross-service error spike</Badge>
            : <Badge tone="neutral">No cross-service spike in this window</Badge>}
        </p>
      </Card>
      <div className="two-col">
        <Card question="How important is it?" title="Triage: workflow & priority"
              actions={<InfoTip text="Workflow comes from endpoint/service keywords. Priority is a configurable rule-based score (POC heuristic), not decided by AI." />}>
          <dl className="facts compact">
            <div><dt>Workflow</dt><dd>{i.workflow}</dd></div>
            <div><dt>Criticality</dt><dd><Badge tone={criticalityTone(i.workflow_criticality)}>{i.workflow_criticality}</Badge></dd></div>
            <div><dt>Priority</dt><dd><Badge tone={priorityTone(i.priority)}>{i.priority}</Badge> score {i.priority_score}/100</dd></div>
          </dl>
          <p className="muted small">{i.workflow_reason}</p>
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Factor</th><th>Observed</th><th>Weight</th><th>Points</th></tr></thead>
              <tbody>
                {i.priority_factors.map((f) => (
                  <tr key={f.name}><td>{f.name.replace(/_/g, " ")}</td><td className="small">{f.value}</td><td>{f.weight}</td><td>{f.points}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>

        <Card question="Is it isolated?" title="Related errors in the same window"
              actions={<InfoTip text="Other abnormal log lines in the incident window (±2 min). Only 'overlapping incidents' are incidents; the rest are individual log lines." />}>
          <p className="small">
            {evidence.related_errors.other_abnormal_logs_in_window} other abnormal log lines on{" "}
            {evidence.related_errors.services_with_errors_in_window.join(", ")}.{" "}
            {spike.incident_events_inside_spike_minutes} of {i.occurrences} events fall inside spike minutes.
          </p>
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Service</th><th>Error type</th><th>Log lines</th></tr></thead>
              <tbody>
                {evidence.related_errors.by_service_and_type.slice(0, 8).map((r) => (
                  <tr key={`${r.service}-${r.error_type}`}><td>{r.service}</td><td className="small">{r.error_type}</td><td>{r.count}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
          {evidence.related_errors.overlapping_incidents.length > 0 && (
            <p className="small"><strong>Overlapping incidents:</strong>{" "}
              {evidence.related_errors.overlapping_incidents.map((o) => `${o.incident_id} (${o.priority}, ${o.service} ${o.error_type})`).join("; ")}
            </p>
          )}
        </Card>
      </div>
    </>
  );
}
