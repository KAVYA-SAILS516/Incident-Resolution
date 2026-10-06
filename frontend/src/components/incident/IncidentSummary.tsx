import type { IncidentDetail } from "../../types";
import { Card } from "../shared/common";

const pct = (v: number | undefined) => (v === undefined ? "—" : `${(v * 100).toFixed(1)}%`);

/** What is happening, in one sentence plus the key measurements (all from existing incident data). */
export function IncidentSummary({ detail, onViewEvidence }: { detail: IncidentDetail; onViewEvidence: () => void }) {
  const { incident: i, evidence } = detail;
  const rt = evidence.statistics?.response_time_ms ?? {};
  const latency = rt.incident_requests;
  const normal = rt.service_normal_requests;
  const spike = evidence.related_errors.cross_service_spike;
  const codes = Object.keys(i.status_codes).join(", ") || "—";

  return (
    <Card question="What is happening?" title="Incident summary">
      <p className="lead">
        {i.service} is returning {i.error_type.replace(/_/g, " ").toLowerCase()} ({codes}) on{" "}
        {i.affected_endpoints.join(", ")}: {i.occurrences} events over {Math.max(1, Math.round(i.duration_seconds / 60))} min
        {spike.spike_minutes_in_window.length > 0
          ? `, overlapping a cross-service error spike (${spike.incident_events_inside_spike_minutes} of ${i.occurrences} events inside it).`
          : ", with no cross-service spike in the same window."}
      </p>
      <div className="metric-tiles">
        <Metric label={`${i.service} abnormal rate`} value={pct(evidence.baseline.service_abnormal_rate_in_window)}
                sub={`vs ${pct(evidence.baseline.service_abnormal_rate_overall)} across the whole log`} />
        <Metric label="Latency p50" value={latency ? `${latency.p50} ms` : "—"}
                sub={normal ? `normal p50 ${normal.p50} ms` : undefined} />
        <Metric label="Latency p95" value={latency ? `${latency.p95} ms` : "—"}
                sub={normal ? `normal p95 ${normal.p95} ms` : undefined} />
        <Metric label="Occurrences" value={String(i.occurrences)} sub={`peak ${i.peak_per_minute}/min`} />
        <Metric label="Hosts affected" value={String(i.hosts.length)} sub={i.hosts.join(", ")} />
        <Metric label="Distinct clients" value={String(i.distinct_clients)} sub={`severity ${i.severity}`} />
      </div>
      <p className="small">
        <button type="button" className="link-button small" onClick={onViewEvidence}>
          View {evidence.representative_logs.length} representative log lines
        </button>
      </p>
    </Card>
  );
}

function Metric({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="metric">
      <span className="metric-label">{label}</span>
      <span className="metric-value">{value}</span>
      {sub && <span className="metric-sub">{sub}</span>}
    </div>
  );
}
