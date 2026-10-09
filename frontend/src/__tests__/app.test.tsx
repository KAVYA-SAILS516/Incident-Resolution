import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { ChatWidget } from "../components/chat/ChatWidget";
import { Dashboard } from "../components/dashboard/Dashboard";
import { DecisionSafetyCard } from "../components/incident/DecisionSafetyCard";
import { PostDecisionStages } from "../components/incident/PostDecisionStages";
import { lifecycleOfDetail, lifecycleOfRow } from "../lib/lifecycle";
import { InvestigationCard } from "../components/incident/InvestigationCard";
import { RecommendationsCard } from "../components/incident/RecommendationsCard";
import { IncidentOverview } from "../components/incident/IncidentOverview";
import { Header } from "../components/layout/Header";
import { TicketList } from "../components/incidents/TicketList";
import { applyTheme, loadTheme } from "../lib/theme";
import type { Dashboard as DashboardData, Incident, IncidentAnalysis, IncidentDetail, Investigation, NotEnabled } from "../types";

const NOT_ENABLED: NotEnabled = { available: false, note: "Not enabled in this POC." };

function incident(overrides: Partial<Incident> = {}): Incident {
  return {
    incident_id: "INC-AC837A74", status: "open", title: "DATABASE_TIMEOUT on order-service /orders",
    service: "order-service", environment: "production-simulated", error_type: "DATABASE_TIMEOUT", status_code: 503,
    endpoint_family: "/orders", affected_endpoints: ["/api/v1/orders"], occurrences: 7,
    first_seen: "2026-09-01T10:44:04Z", last_seen: "2026-09-01T11:04:10Z", duration_seconds: 1206,
    severity: "CRITICAL", levels: { ERROR: 5, CRITICAL: 2 }, status_codes: { "503": 7 }, hosts: ["app-02", "app-03"],
    distinct_clients: 7, peak_per_minute: 2, workflow: "Checkout/Order", workflow_criticality: "CRITICAL",
    workflow_reason: "Matched order", priority: "P1", priority_score: 84.5, priority_reasons: ["score 84.5/100 -> P1"],
    priority_factors: [{ name: "workflow_criticality", value: "Checkout/Order = CRITICAL (5/5)", normalized: 1, weight: 0.4, points: 40 }],
    // The backend has not analysed this incident yet; the background workflow has it queued.
    analysis: { state: "queued", error: null },
    ...overrides,
  };
}

function detail(overrides: Partial<IncidentDetail> = {}): IncidentDetail {
  return {
    incident: incident(),
    evidence: {
      facts: ["7 DATABASE_TIMEOUT events on order-service."],
      statistics: {},
      timeline: { per_minute: [], peak_per_minute: 2, duration_seconds: 1206 },
      baseline: {},
      related_errors: {
        window: { start: "", end: "" },
        cross_service_spike: { method: "", spike_minutes_in_window: ["2026-09-01T10:45:00Z"],
          incident_events_inside_spike_minutes: 3, incident_events_outside_spike_minutes: 4 },
        other_abnormal_logs_in_window: 117, services_with_errors_in_window: ["order-service", "payment-service"],
        by_service_and_type: [{ service: "payment-service", error_type: "SLOW_REQUEST", count: 9 }],
        overlapping_incidents: [],
      },
      representative_logs: [],
      runbook: { available: true, runbook_id: "RB-DATABASE-TIMEOUT" },
      data_limits: { log_lines_in_incident: 7, log_lines_shared_with_ai: 6, not_in_logs: ["infrastructure metrics"] },
    },
    investigation: null,
    recommendations: null,
    ...overrides,
  };
}

const investigation: Investigation = {
  incident_id: "INC-AC837A74", root_cause: "Database connection pool exhaustion", confidence: 0.6,
  evidence: [
    { statement: "All 7 events are HTTP 503", kind: "FACT" as const, source: "get_log_statistics" },
    { statement: "Likely a shared database", kind: "AI_INFERENCE" as const, source: "" },
  ],
  analysis: "Timeouts cluster during the spike.", unknowns: ["Database server metrics"], insufficient_evidence: false,
  model: "gemini-2.5-flash", tools_called: ["get_log_statistics"], based_on_occurrences: 7, duration_ms: 1000,
  created_at: "2026-09-25T12:00:00Z",
};

// An incident whose investigation and recommendations both exist (the finished, "done" state).
function analysedIncident(overrides: Partial<IncidentDetail> = {}): IncidentDetail {
  const recommendations = {
    incident_id: "INC-AC837A74", model: "gemini-2.5-flash", runbook_id: "RB-DATABASE-TIMEOUT", tools_called: [],
    validation_notes: [], duration_ms: 1000, created_at: "2026-09-25T12:00:00Z",
    recommendations: [
      { title: "Check database connection pool saturation", description: "d", reason: "r", risk: "LOW" as const, source: "runbook" as const },
      { title: "Fail over to a healthy database replica", description: "d2", reason: "r2", risk: "HIGH" as const, source: "AI-generated" as const },
    ],
  };
  return detail({
    incident: incident({ status: "recommendations_ready", analysis: { state: "done", error: null } }),
    investigation, recommendations, ...overrides,
  });
}

describe("theme", () => {
  it("switches between light and dark, defaults to light and remembers the choice", () => {
    window.localStorage.clear();
    expect(loadTheme()).toBe("light");
    applyTheme("dark");
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    applyTheme("light");
    expect(loadTheme()).toBe("light");
  });

  it("header shows the five sections and the Vertex AI state", () => {
    render(<Header tab="dashboard" onTab={() => {}} theme="light" onTheme={() => {}}
                   health={{ status: "ok", llm_configured: true, model: "gemini-2.5-flash", logs_ingested: 5000, incidents: 21 }} />);
    for (const name of ["Dashboard", "Incidents", "Applications", "Agent Activity", "Knowledge"]) {
      expect(screen.getByRole("button", { name })).toBeInTheDocument();
    }
    expect(screen.getByText(/gemini-2.5-flash: Configured/)).toBeInTheDocument();
  });
});

describe("dashboard (command center)", () => {
  const data: DashboardData = {
    ingested: true,
    ingest: { sources: ["opentelemetry"], total_lines: 5000, parsed_lines: 5000, failed_lines: 0, error_logs: 304,
      warning_logs: 209, services: ["order-service"], environments: ["production-simulated"],
      time_range: { start: null, end: null }, incidents_detected: 21, below_threshold_groups: 183, failures_sample: [],
      ingested_at: "2026-09-25T12:00:00Z" },
    incidents: { total: 21, by_priority: { P1: 3, P2: 7, P3: 9, P4: 2 }, by_workflow: { "Checkout/Order": 9 },
      by_criticality: {}, by_service: {}, by_error_type: {}, by_status: {} },
    agents: { investigated: 1, recommendations_ready: 0, llm_configured: true, auto_analyze: true, model: "gemini-2.5-flash",
      queue: { queued: 20, investigating: 1, recommending: 0, failed: 0 } },
    top_incidents: [incident()],
    timeline: { bucket_minutes: 5, points: [] },
    analytics: {
      incident_volume: 21,
      priority_distribution: { P1: 3, P2: 7, P3: 9, P4: 2 },
      workflow_stage_distribution: { queued: 20, investigating: 1, recommending: 0, done: 0, failed: 0, disabled: 0 },
      mttd: NOT_ENABLED, mttr: NOT_ENABLED, resolution_rate: NOT_ENABLED, auto_remediation_count: NOT_ENABLED,
      human_approval_count: NOT_ENABLED, human_takeover_count: NOT_ENABLED, verification_outcomes: NOT_ENABLED,
    },
  };

  it("is a command center: just Detected and Human decision in the funnel, separate criticality/priority, status", () => {
    const onIngest = vi.fn();
    const onShow = vi.fn();
    const withStatus = { ...data, incidents: { ...data.incidents, by_status: { open: 15, investigated: 1, recommendations_ready: 5 } } };
    render(<Dashboard data={withStatus} onIngest={onIngest} ingesting={false} onOpen={() => {}} onShowList={onShow} />);
    expect(screen.getByText("Command Center")).toBeInTheDocument();
    expect(screen.getByText("INC-AC837A74")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Criticality" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Priority" })).toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: "AI stage" })).not.toBeInTheDocument();
    expect(screen.getByText("Background workflow queued")).toBeInTheDocument(); // the row's real analysis.state, not "opened"
    // Triaged / AI working / AI investigated / Remediated / Verified / Resolved were removed from the funnel.
    for (const label of ["Triaged", "AI working", "AI investigated", "Remediated", "Verified", "Resolved"]) {
      expect(screen.queryByText(label)).not.toBeInTheDocument();
    }
    fireEvent.click(screen.getByRole("button", { name: /Human decision/ }));
    expect(onShow).toHaveBeenCalledWith({ status: "recommendations_ready" });
    fireEvent.click(screen.getByRole("button", { name: "Show P4 incidents" }));
    expect(onShow).toHaveBeenCalledWith({ priority: "P4" });
    fireEvent.click(screen.getByRole("button", { name: "Read Telemetry" }));
    expect(onIngest).toHaveBeenCalled();
  });
});

describe("incident list", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("lists incidents with workflow, criticality, occurrences, priority and the real backend analysis state", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ total: 1, incidents: [incident()] }))));
    render(<TicketList onOpen={() => {}} refreshKey={0} />);
    await waitFor(() => expect(screen.getAllByText("INC-AC837A74").length).toBeGreaterThan(0));
    expect(screen.getByText("Checkout/Order", { selector: "td" })).toBeInTheDocument();
    expect(screen.getAllByText("CRITICAL").length).toBeGreaterThan(0);
    expect(screen.getByText("Background workflow queued")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /P4/ })).toBeInTheDocument();
  });
});

describe("incident detail: investigation and recommendations only ever display backend state", () => {
  it("overview shows deterministic facts, priority factors and the spike flag", () => {
    render(<IncidentOverview detail={detail()} />);
    expect(screen.getByText(/7 DATABASE_TIMEOUT events/)).toBeInTheDocument();
    expect(screen.getByText("Overlaps a cross-service error spike")).toBeInTheDocument();
    expect(screen.getByText("workflow criticality")).toBeInTheDocument();
  });

  it("has no agent buttons, and shows progress exactly when the backend reports it is investigating", () => {
    render(<InvestigationCard detail={detail({ incident: incident({ analysis: { state: "investigating", error: null } }) })} />);
    expect(screen.queryByRole("button", { name: /Investigate/ })).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Investigation Agent is analysing");
  });

  it("never claims to be waiting on the user: shows the real disabled/failed reason from the backend", () => {
    const { rerender } = render(<InvestigationCard detail={detail({ incident: incident({ analysis: { state: "disabled", error: "Vertex AI is not configured" } }) })} />);
    expect(screen.getByText(/Vertex AI is not configured/)).toBeInTheDocument();

    rerender(<InvestigationCard detail={detail({ incident: incident({ analysis: { state: "failed", error: "model timed out" } }) })} />);
    expect(screen.getByText(/Automatic analysis failed: model timed out/)).toBeInTheDocument();
  });

  it("separates facts, AI inference, root cause, confidence and unknowns once the backend has results", () => {
    render(<InvestigationCard detail={detail({ investigation })} />);
    expect(screen.getByText("All 7 events are HTTP 503")).toBeInTheDocument();
    expect(screen.getByText("Likely a shared database")).toBeInTheDocument();
    expect(screen.getByText("Database connection pool exhaustion")).toBeInTheDocument();
    expect(screen.getByText("60%")).toBeInTheDocument();
    expect(screen.getByText(/Database server metrics/)).toBeInTheDocument();
    expect(screen.getByText("Investigation & root cause")).toBeInTheDocument();
  });

  it("recommendations have no button and reflect the backend queue, not page-open state", () => {
    render(<RecommendationsCard detail={detail()} selectedIndex={0} onSelect={() => {}} />);
    expect(screen.queryByRole("button", { name: /recommendations/i })).not.toBeInTheDocument();
    expect(screen.getByText(/Waiting for the investigation to finish/)).toBeInTheDocument();
  });

  it("shows recommendations with risk and source, and marks the selected one", () => {
    const onSelect = vi.fn();
    render(<RecommendationsCard detail={analysedIncident()} selectedIndex={0} onSelect={onSelect} />);
    expect(screen.getByText("1. Check database connection pool saturation")).toBeInTheDocument();
    expect(screen.getByText("RUNBOOK")).toBeInTheDocument();
    expect(screen.getByText(/Nothing below has been executed/)).toBeInTheDocument();
    expect(screen.getByText("Selected")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Fail over to a healthy database replica/ }));
    expect(onSelect).toHaveBeenCalledWith(1);
  });
});

describe("lifecycle (only what the backend persists)", () => {
  it("row view derives stages from incident.status and incident.analysis, never from 'has the page been opened'", () => {
    expect(lifecycleOfRow({ status: "open", analysis: { state: "queued", error: null } }).aiStage).toBe("Queued for analysis");
    expect(lifecycleOfRow({ status: "open", analysis: { state: "investigating", error: null } }).aiStage).toBe("Investigating");
    expect(lifecycleOfRow({ status: "open", analysis: { state: "disabled", error: "off" } }).aiStage).toBe("AI analysis disabled");
    expect(lifecycleOfRow({ status: "investigated", analysis: { state: "done", error: null } }).aiStage).toBe("RCA identified");
    const ready = lifecycleOfRow({ status: "recommendations_ready", analysis: { state: "done", error: null } });
    expect(ready.aiStage).toBe("Recommendations ready");
    expect(ready.status).toBe("Human decision needed");
  });

  it("detail view can distinguish an inconclusive RCA", () => {
    const done: IncidentAnalysis = { state: "done", error: null };
    expect(lifecycleOfDetail({ analysis: done }, { ...investigation, insufficient_evidence: true }, null).aiStage).toBe("RCA inconclusive");
    expect(lifecycleOfDetail({ analysis: { state: "investigating", error: null } }, null, null)
      .steps.find((s) => s.key === "investigate")!.state).toBe("active");
  });

  it("never marks remediation, verification or closure as done, for any state", () => {
    const states: IncidentAnalysis[] = [{ state: "queued", error: null }, { state: "done", error: null }, { state: "failed", error: "x" }];
    for (const analysis of states) {
      const lc = lifecycleOfRow({ status: "recommendations_ready", analysis });
      for (const key of ["remediate", "verify", "close"]) {
        expect(lc.steps.find((s) => s.key === key)!.state).toBe("disabled");
      }
    }
  });
});

describe("decision & safety", () => {
  const twoOptions = analysedIncident();

  it("requires human approval, disables execution and wires Investigate more as an explicit action", () => {
    const more = vi.fn();
    render(<DecisionSafetyCard detail={twoOptions} selected={twoOptions.recommendations!.recommendations[0]}
                               busy={false} onInvestigateMore={more} />);
    expect(screen.getByText("⚠ HUMAN APPROVAL REQUIRED")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Take over" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Investigate more" }));
    expect(more).toHaveBeenCalled();
  });

  it("reflects whichever recommendation is selected, not always the first", () => {
    const { rerender } = render(<DecisionSafetyCard detail={twoOptions} selected={twoOptions.recommendations!.recommendations[0]}
                                                     busy={false} onInvestigateMore={() => {}} />);
    expect(screen.getByText("Check database connection pool saturation")).toBeInTheDocument();
    expect(screen.getByText("LOW")).toBeInTheDocument();

    rerender(<DecisionSafetyCard detail={twoOptions} selected={twoOptions.recommendations!.recommendations[1]}
                                 busy={false} onInvestigateMore={() => {}} />);
    expect(screen.getByText("Fail over to a healthy database replica")).toBeInTheDocument();
    expect(screen.getByText("HIGH")).toBeInTheDocument();
  });

  it("post-decision stages never claim execution or resolution", () => {
    const { container } = render(<PostDecisionStages detail={analysedIncident()} />);
    expect(container.textContent).toContain("Nothing has been executed, verified or closed");
    expect(container.textContent).not.toMatch(/VERIFIED RESOLVED|✓ Executed|✓ Verified/);
  });
});

describe("chat widget (dashboard-wide Chat Agent)", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("is closed by default, opens on click, sends a message and shows the reply with a clickable incident chip", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      expect(url).toContain("/api/chat");
      expect(JSON.parse(String(init?.body)).message).toBe("which p1s are open?");
      return new Response(JSON.stringify({
        reply: "INC-AC837A74 is the only open P1.", referenced_incidents: ["INC-AC837A74"],
        model: "gemini-2.5-flash", tools_called: ["list_incidents"], duration_ms: 10,
      }));
    }));
    const onOpen = vi.fn();
    render(<ChatWidget onOpenIncident={onOpen} />);
    expect(screen.queryByLabelText("Chat message")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /open chat/i }));
    fireEvent.change(screen.getByLabelText("Chat message"), { target: { value: "which p1s are open?" } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));

    await waitFor(() => expect(screen.getByText("INC-AC837A74 is the only open P1.")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "INC-AC837A74" }));
    expect(onOpen).toHaveBeenCalledWith("INC-AC837A74");
  });

  it("shows a backend error inline instead of crashing", async () => {
    vi.stubGlobal("fetch", vi.fn(async () =>
      new Response(JSON.stringify({ detail: "Vertex AI is not configured" }), { status: 503, statusText: "Service Unavailable" })));
    render(<ChatWidget onOpenIncident={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: /open chat/i }));
    fireEvent.change(screen.getByLabelText("Chat message"), { target: { value: "hello" } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => expect(screen.getByText(/Vertex AI is not configured/)).toBeInTheDocument());
  });
});

describe("security", () => {
  it("frontend source contains no credentials or direct Vertex AI access", () => {
    const root = join(__dirname, "..");
    const files: string[] = [];
    const walk = (dir: string) => readdirSync(dir).forEach((f) => {
      const p = join(dir, f);
      if (statSync(p).isDirectory()) { if (f !== "__tests__") walk(p); } else files.push(p);
    });
    walk(root);
    const source = files.map((f) => readFileSync(f, "utf8")).join("\n");
    for (const forbidden of ["GOOGLE_APPLICATION_CREDENTIALS", "private_key", "aiplatform.googleapis.com", "@google/genai"]) {
      expect(source).not.toContain(forbidden);
    }
  });
});
