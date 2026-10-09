// Where an incident is in the resolution lifecycle. This reads ONLY data the backend persists
// (incident.status, incident.analysis, and - on the detail page - the investigation/recommendations
// themselves). The UI never infers a stage from whether a page happened to be opened: the background
// workflow (backend/app/agents/auto_analysis.py) runs independently of the UI, so the same incident
// shows the same stage whether or not anyone is looking at it.
import type { AnalysisState, Incident, Investigation, Recommendations, Resolution } from "../types";

export type StepState = "done" | "active" | "pending" | "disabled";

export interface Step {
  key: string;
  label: string;
  state: StepState;
  note: string;
}

export interface Lifecycle {
  aiStage: string;
  stageTone: string;
  status: string;
  statusTone: string;
  needsHuman: boolean;
  steps: Step[];
}

export const NOT_IN_POC = "Not enabled in this POC";

function build(state: AnalysisState, investigated: boolean, inconclusive: boolean, recommended: boolean,
               resolution: Resolution | null = null): Lifecycle {
  let aiStage: string, stageTone: string, status: string, statusTone: string;

  if (resolution?.state === "resolved") {
    aiStage = "Resolved"; stageTone = "success"; status = "Recovery verified"; statusTone = "success";
  } else if (resolution?.state === "human_takeover") {
    aiStage = "Human takeover"; stageTone = "danger"; status = "A person owns this incident"; statusTone = "danger";
  } else if (resolution?.state === "executed") {
    aiStage = "Verifying"; stageTone = "info"; status = "Action executed; verifying recovery"; statusTone = "info";
  } else if (recommended) {
    aiStage = "Recommendations ready"; stageTone = "info";
    status = "Human decision needed"; statusTone = "warning";
  } else if (state === "investigating") {
    aiStage = "Investigating"; stageTone = "info"; status = "Background workflow running"; statusTone = "info";
  } else if (state === "recommending") {
    aiStage = "Generating recommendations"; stageTone = "info"; status = "Background workflow running"; statusTone = "info";
  } else if (investigated) {
    aiStage = inconclusive ? "RCA inconclusive" : "RCA identified"; stageTone = inconclusive ? "warning" : "info";
    status = "Awaiting recommendations"; statusTone = "info";
  } else if (state === "failed") {
    aiStage = "Analysis failed"; stageTone = "danger"; status = "Needs attention"; statusTone = "danger";
  } else if (state === "disabled") {
    aiStage = "AI analysis disabled"; stageTone = "neutral"; status = "Not analysed (disabled)"; statusTone = "neutral";
  } else {
    aiStage = "Queued for analysis"; stageTone = "neutral"; status = "Background workflow queued"; statusTone = "neutral";
  }

  const investigateState: StepState = investigated ? "done" : state === "investigating" ? "active" : "pending";
  const decided = !!resolution && resolution.state !== "proposed";
  const decideState: StepState = decided ? "done" : recommended ? "active" : state === "recommending" ? "active" : "pending";
  const steps: Step[] = [
    { key: "detect", label: "Detect", state: "done", note: "Abnormal log events found by rules" },
    { key: "correlate", label: "Correlate", state: "done", note: "Grouped by service, error type, endpoint and time window" },
    { key: "triage", label: "Triage", state: "done", note: "Workflow, criticality and priority assigned by rules" },
    { key: "investigate", label: "Investigate", state: investigateState,
      note: investigated ? "Investigation Agent finished" : state === "investigating" ? "Investigation Agent running (background)"
          : state === "failed" ? "Failed - see below" : "Waiting in the background queue" },
    { key: "rca", label: "RCA", state: investigated ? "done" : "pending",
      note: investigated ? (inconclusive ? "Inconclusive: insufficient evidence" : "Root-cause hypothesis identified") : "No hypothesis yet" },
    { key: "decide", label: "Decide", state: decideState,
      note: decided ? "Decision made" : recommended ? "Recommendations ready; a person must decide" : state === "recommending" ? "Resolution Decision Agent running (background)" : "Waiting for recommendations" },
    ...postDecisionSteps(resolution),
  ];

  return { aiStage, stageTone, status, statusTone, needsHuman: recommended, steps };
}

/** Remediate / Verify / Close from the persisted resolution record (guarded, simulated remediation). */
function postDecisionSteps(resolution: Resolution | null): Step[] {
  if (!resolution) {
    return [
      { key: "remediate", label: "Remediate", state: "disabled", note: NOT_IN_POC },
      { key: "verify", label: "Verify", state: "disabled", note: NOT_IN_POC },
      { key: "close", label: "Close", state: "disabled", note: NOT_IN_POC },
    ];
  }
  const attempts = resolution.attempts.length;
  const last = resolution.verifications[resolution.verifications.length - 1];
  const executed = resolution.state === "executed";
  return [
    { key: "remediate", label: "Remediate", state: executed ? "active" : attempts > 0 ? "done" : "pending",
      note: attempts > 0 ? `${attempts} guarded action(s) executed (simulated)` : "Waiting for approval or takeover" },
    { key: "verify", label: "Verify", state: resolution.state === "resolved" ? "done" : executed ? "active" : "pending",
      note: last ? `Latest: ${last.status}` : "Verified from telemetry after an action" },
    { key: "close", label: "Close", state: resolution.state === "resolved" ? "done" : "pending",
      note: resolution.state === "resolved" ? "Recovery verified" : resolution.state === "human_takeover" ? "Human takeover" : "Not resolved yet" },
  ];
}

/** List/dashboard rows: the incident already carries status and analysis state from the API. */
export function lifecycleOfRow(incident: Pick<Incident, "status" | "analysis">): Lifecycle {
  return build(incident.analysis.state, incident.status !== "open", false, incident.status === "recommendations_ready");
}

/** Incident detail page: the full investigation/recommendations are available, so RCA can say "inconclusive". */
export function lifecycleOfDetail(incident: Pick<Incident, "analysis">, investigation: Investigation | null,
                                  recommendations: Recommendations | null, resolution: Resolution | null = null): Lifecycle {
  return build(incident.analysis.state, !!investigation, !!investigation?.insufficient_evidence, !!recommendations, resolution);
}
