import type { IncidentDetail } from "../../types";
import { Card } from "../shared/common";

/** Remediation, verification and closure: shown so the lifecycle is visible, never marked as happened. */
export function PostDecisionStages({ detail }: { detail: IncidentDetail }) {
  const { incident: i, evidence, recommendations } = detail;
  const latency = evidence.statistics?.response_time_ms?.incident_requests;
  const rate = evidence.baseline.service_abnormal_rate_in_window;

  return (
    <Card question="What happens after the decision?" title="Remediation, verification & closure">
      <p className="muted small">Not enabled in this POC. Nothing has been executed, verified or closed.</p>
      <details>
        <summary>Remediation: not started</summary>
        <ul className="ladder" aria-label="Remediation states">
          <li className={recommendations ? "l-done" : "l-pending"}>{recommendations ? "✓" : "○"} Recommended</li>
          <li className="l-pending">○ Approved</li>
          <li className="l-pending">○ Executing</li>
          <li className="l-pending">○ Executed</li>
          <li className="l-pending">○ Verified</li>
        </ul>
        <p className="small">Recommended, approved, executing, executed and verified are separate states. Only the first applies here.</p>
      </details>
      <details>
        <summary>Verification: not started</summary>
        <p className="small"><strong>Execution success ≠ incident resolution.</strong> Verification would compare these values after an action:</p>
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Signal</th><th>Before (now)</th><th>After</th></tr></thead>
            <tbody>
              <tr><td>{i.service} abnormal rate</td><td>{rate === undefined ? "—" : `${(rate * 100).toFixed(1)}%`}</td><td className="muted">—</td></tr>
              <tr><td>Latency p95</td><td>{latency ? `${latency.p95} ms` : "—"}</td><td className="muted">—</td></tr>
              <tr><td>{i.error_type} events</td><td>{i.occurrences}</td><td className="muted">—</td></tr>
            </tbody>
          </table>
        </div>
      </details>
      <details>
        <summary>Closure & learning: not closed</summary>
        <p className="small">Closing the incident and capturing the confirmed root cause as knowledge are not enabled in this POC.</p>
      </details>
    </Card>
  );
}
