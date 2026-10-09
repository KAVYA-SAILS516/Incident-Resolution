import { useState } from "react";
import { api } from "../../api";
import type { ExecutionMode, IncidentDetail, Resolution } from "../../types";
import { Badge, Card, Confidence, InfoTip, riskTone } from "../shared/common";

const MODE_LABEL: Record<ExecutionMode, string> = {
  AUTO_EXECUTE: "Runs automatically",
  HUMAN_APPROVAL: "Needs approval",
  HUMAN_TAKEOVER: "A person must act",
};
const modeTone = (m: ExecutionMode) => (m === "AUTO_EXECUTE" ? "success" : m === "HUMAN_APPROVAL" ? "warning" : "danger");

/** Resolution decision: the options, the AI recommendation, who acts, and what happens next. */
export function ResolutionCard({ detail, onChanged }: { detail: IncidentDetail; onChanged: () => void }) {
  const resolution = detail.resolution;
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [approver, setApprover] = useState(() => { try { return window.localStorage.getItem("approver") ?? ""; } catch { return ""; } });
  if (!resolution) return null;
  const id = detail.incident.incident_id;

  const run = async (action: () => Promise<unknown>, done: (r: unknown) => string) => {
    setBusy(true);
    setMessage(null);
    try {
      setMessage(done(await action()));
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
      onChanged();
    }
  };

  const recommended = resolution.options.find((o) => o.option_id === resolution.recommended_option_id) ?? null;
  const waitingForVerification = resolution.state === "executed";
  const closed = resolution.state === "resolved" || resolution.state === "human_takeover";
  const needsApprover = recommended?.execution_mode === "HUMAN_APPROVAL";
  const canApprove = !!recommended && !waitingForVerification && !closed && recommended.execution_mode !== "HUMAN_TAKEOVER";

  return (
    <Card
      question="What can we do, and what happens next?"
      title="Resolution"
      actions={<InfoTip text="Options come only from a short list of registered, guarded actions. Nothing runs without the policy shown here; in this proof of concept actions are simulated, and recovery is judged from real telemetry afterwards." />}
    >
      <div className="working neutral" role="status">
        <Badge tone={modeTone(resolution.decision)}>{MODE_LABEL[resolution.decision]}</Badge>{" "}
        {resolution.decision_reason} <strong>Next:</strong> {resolution.next_step}
      </div>

      <p className="muted small">
        {resolution.reasoned_by === "ai+policy" ? "Chosen by the AI among policy-approved options; policy sets the execution mode." : "Ranked by policy (no AI choice recorded)."}
        {(resolution.validation_notes ?? []).map((n) => <span key={n}> {n}</span>)}
      </p>

      {resolution.options.length === 0
        ? <p className="muted small">No registered action applies to this service, so a person needs to resolve it.</p>
        : (
          <div className="option-list">
            {resolution.options.map((o) => {
              const best = o.option_id === resolution.recommended_option_id;
              return (
                <div key={o.option_id} className={`option-card${best ? " option-selected" : ""}`}>
                  <div className="option-head">
                    <h3>{o.title || o.description}</h3>
                    {best && <Badge tone="info">AI recommendation</Badge>}
                  </div>
                  <div className="row option-facts" style={{ flexWrap: "wrap" }}>
                    <Badge tone={riskTone(o.risk)}>{o.risk} risk</Badge>
                    <Badge tone={riskTone(o.blast_radius_level)}>{o.blast_radius_level} blast radius</Badge>
                    <Badge tone="neutral">{o.reversibility.toLowerCase()}</Badge>
                    <Badge tone={modeTone(o.execution_mode)}>{MODE_LABEL[o.execution_mode]}</Badge>
                    <Confidence value={o.confidence} />
                  </div>
                  <p className="small">{o.description}</p>
                  <p className="small"><strong>Impact:</strong> {o.impact}</p>
                  {o.expected_outcome && <p className="small"><strong>Expected outcome:</strong> {o.expected_outcome}</p>}
                  {(o.prerequisites ?? []).length > 0 && <p className="small"><strong>Prerequisites:</strong> {o.prerequisites!.join("; ")}</p>}
                  <p className="small"><strong>Blast radius:</strong> {o.blast_radius}</p>
                </div>
              );
            })}
          </div>
        )}

      {needsApprover && canApprove && (
        <label className="stack" style={{ gap: "0.2rem", maxWidth: 320 }}>
          <span className="field-label">Approver (your name, recorded in the audit)</span>
          <input value={approver} aria-label="Approver" placeholder="e.g. alice@example.com"
                 onChange={(e) => { setApprover(e.target.value); try { window.localStorage.setItem("approver", e.target.value); } catch { /* ignore */ } }} />
        </label>
      )}
      <div className="button-row">
        <button type="button" disabled={busy || !canApprove || (needsApprover && !approver.trim())}
                title={needsApprover && !approver.trim() ? "Enter the approver's name first" : undefined}
                onClick={() => recommended && run(() => api.approve(id, recommended.option_id, approver.trim() || "policy"), () => "Action recorded (simulated). Verify recovery once new telemetry has arrived.")}>
          {recommended?.execution_mode === "AUTO_EXECUTE" ? "Run recommended action" : "Approve recommended action"}
        </button>
        <button type="button" disabled={busy || !waitingForVerification}
                onClick={() => run(() => api.verify(id), (r) => { const v = r as { status: string; reason: string }; return `${v.status}: ${v.reason}`; })}>
          Verify recovery
        </button>
        <button type="button" disabled={busy || resolution.state === "human_takeover"}
                onClick={() => run(() => api.takeOver(id), () => "A person now owns this incident.")}>
          Take over
        </button>
      </div>
      {message && <p className="small" role="status">{message}</p>}

      {(resolution.attempts.length > 0 || resolution.verifications.length > 0) && <History resolution={resolution} />}
    </Card>
  );
}

function History({ resolution }: { resolution: Resolution }) {
  return (
    <details open>
      <summary>What has been tried ({resolution.attempts.length})</summary>
      <ul className="plain">
        {resolution.attempts.map((a) => {
          const v = resolution.verifications.find((x) => x.attempt_id === a.attempt_id);
          return (
            <li key={a.attempt_id} className="small">
              {a.detail} <span className="muted">(approved by {a.approved_by ?? "n/a"})</span>{" "}
              {v ? <Badge tone={v.status === "SUCCESS" ? "success" : v.status === "FAILED" ? "danger" : "neutral"}>{v.status}</Badge>
                 : <Badge tone="neutral">not verified yet</Badge>}
              {v && <div className="muted">{v.reason}</div>}
            </li>
          );
        })}
      </ul>
    </details>
  );
}
