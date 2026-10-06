import type { Lifecycle } from "../../lib/lifecycle";
import { Card, InfoTip } from "../shared/common";

const ICON = { done: "✓", active: "●", pending: "○", disabled: "–" } as const;

/** Detect → Correlate → Triage → Investigate → RCA → Decide → Remediate → Verify → Close. */
export function LifecycleStepper({ lifecycle }: { lifecycle: Lifecycle }) {
  return (
    <Card
      question="Where is this incident in the lifecycle?"
      title="Resolution lifecycle"
      actions={<InfoTip text="Detect, correlate and triage are done by rules; investigate/RCA and recommendations by the two AI agents. A person decides. Remediation, verification and closure are not enabled in this POC." />}
    >
      <ol className="stepper" aria-label="Lifecycle stages">
        {lifecycle.steps.map((s) => (
          <li key={s.key} className={`s-${s.state}`} aria-current={s.state === "active" ? "step" : undefined}>
            <span className="step-dot" aria-hidden>{ICON[s.state]}</span>
            <span className="step-label">{s.label}</span>
            <span className="step-note">{s.note}</span>
          </li>
        ))}
      </ol>
    </Card>
  );
}
