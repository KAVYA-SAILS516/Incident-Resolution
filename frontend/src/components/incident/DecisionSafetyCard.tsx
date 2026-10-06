import type { IncidentDetail, Recommendation } from "../../types";
import { Badge, Card, Confidence, criticalityTone, InfoTip, riskTone } from "../shared/common";

const NOT_ENABLED = "Not enabled in this POC: there is no execution or ownership backend.";

/** The selected recommendation → its risk → policy → human decision. Nothing here executes anything. */
export function DecisionSafetyCard({ detail, selected, busy, onInvestigateMore }: {
  detail: IncidentDetail; selected: Recommendation | null; busy: boolean; onInvestigateMore: () => void;
}) {
  const { incident: i, investigation } = detail;
  if (!selected || !investigation) return null;

  return (
    <Card
      question="Can the selected action be taken?"
      title="Decision & safety"
      actions={<InfoTip text="Risk and policy for the recommendation selected above. This POC has no policy engine or executor, so every action needs a person and nothing is executed." />}
    >
      <div className="decision-grid">
        <div>
          <span className="field-label">Selected action</span>
          <p className="lead">{selected.title}</p>
          <dl className="facts compact">
            <div><dt>Action risk</dt><dd><Badge tone={riskTone(selected.risk)}>{selected.risk}</Badge></dd></div>
            <div><dt>Business criticality</dt><dd><Badge tone={criticalityTone(i.workflow_criticality)}>{i.workflow_criticality}</Badge> {i.workflow}</dd></div>
            <div><dt>Blast radius</dt><dd>{i.service} · {i.hosts.length} host(s) · {i.distinct_clients} clients</dd></div>
            <div><dt>Reversibility / rollback</dt><dd className="muted">Not assessed (no change or deployment data)</dd></div>
            <div><dt>RCA confidence</dt><dd><Confidence value={investigation.confidence} /></dd></div>
            <div><dt>Source</dt><dd><span className="source-label">{selected.source === "runbook" ? "RUNBOOK" : "AI-GENERATED"}</span></dd></div>
          </dl>
        </div>
        <div className="policy-box" role="status">
          <div className="field-label">Policy</div>
          <div className="policy-title">⚠ HUMAN APPROVAL REQUIRED</div>
          <p className="small">
            This POC never auto-executes. A person reviews the selected action, its {selected.risk} risk and the
            RCA confidence ({Math.round(investigation.confidence * 100)}%) and decides.
          </p>
          <div className="button-row">
            <button type="button" disabled title={NOT_ENABLED}>Approve</button>
            <button type="button" disabled={busy} onClick={onInvestigateMore}>
              {busy ? "Investigating…" : "Investigate more"}
            </button>
            <button type="button" disabled title={NOT_ENABLED}>Take over</button>
          </div>
          <p className="disabled-hint">Approve and Take over are not enabled in this POC. Investigate more re-runs both agents.</p>
        </div>
      </div>
    </Card>
  );
}
