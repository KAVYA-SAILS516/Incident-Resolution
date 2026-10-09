import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ApplicationsPage } from "../components/applications/ApplicationsPage";
import { ApplicationContextCard } from "../components/incident/ApplicationContextCard";
import { ResolutionCard } from "../components/incident/ResolutionCard";
import { KnowledgePage } from "../components/knowledge/KnowledgePage";
import type { IncidentDetail } from "../types";

const PATH = "C:\\incident-data\\astronomy-shop";

const APP_VIEW = {
  scanned: true,
  knowledge: {
    application_name: "OpenTelemetry Astronomy Shop", application_path: PATH, summary: "A demo shop.",
    services: [
      { name: "payment", kind: "application", purpose: "Processes payments.", criticality: "CRITICAL", dependencies: ["flagd"], dependents: ["checkout"], apis: [], failure_scenarios: [] },
      { name: "quote", kind: "application", purpose: null, criticality: null, dependencies: [], dependents: [], apis: [], failure_scenarios: [] },
      { name: "valkey-cart", kind: "infrastructure", purpose: null, criticality: null, dependencies: [], dependents: [], apis: [], failure_scenarios: [] },
    ],
    dependencies: [], apis: [], documentation: [], failure_scenarios: [], scan_warnings: [], files_scanned: 150, last_scanned_at: "2026-10-07T18:00:00Z",
    telemetry: { signals: { logs: { receivers: ["otlp"], exporters: ["opensearch"] }, traces: { receivers: ["otlp"], exporters: ["jaeger"] } }, collector_configs: [] },
  },
  telemetry_runtime: { enabled: true, collector_endpoint: null, logs: true, traces: true, metrics: false },
  policies: [{ priority: "P1", minimum_score: 80 }, { priority: "P2", minimum_score: 65 }, { priority: "P3", minimum_score: 50 }, { priority: "P4", minimum_score: 0 }],
};

describe("applications page", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows missing required configuration instead of hiding it", async () => {
    const issues = ["ASTRONOMY_SHOP_PATH is not configured.", "GOOGLE_CLOUD_PROJECT is not configured."];
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ ...APP_VIEW, scanned: false, knowledge: null, configuration_issues: issues }))));
    render(<ApplicationsPage />);
    await waitFor(() => expect(screen.getByText("ASTRONOMY_SHOP_PATH is not configured.")).toBeInTheDocument());
    expect(screen.getByText(/GOOGLE_CLOUD_PROJECT is not configured/)).toBeInTheDocument();
  });

  it("shows the scanned application, its services (unknown stays unknown) and the P1-P4 policies", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(APP_VIEW))));
    render(<ApplicationsPage />);
    await waitFor(() => expect(screen.getByText("Scanned")).toBeInTheDocument());
    expect(screen.getByText(PATH)).toBeInTheDocument();
    expect(screen.getByText("Services (2)")).toBeInTheDocument(); // infrastructure is not listed as a service
    expect(screen.getByText("CRITICAL")).toBeInTheDocument();
    expect(screen.getAllByText("unknown").length).toBeGreaterThanOrEqual(2);
    for (const p of ["P1", "P2", "P3", "P4"]) expect(screen.getByText(p)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Rescan Application" })).toBeInTheDocument();
  });

  it("sends name and path to the scan API and shows a backend error for a bad path", async () => {
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method === "POST") return new Response(JSON.stringify({ detail: "Path does not exist: C:\\nope" }), { status: 400, statusText: "Bad Request" });
      return new Response(JSON.stringify({ ...APP_VIEW, scanned: false, knowledge: null }));
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ApplicationsPage />);
    expect(await screen.findByRole("button", { name: "Scan Application" })).toBeDisabled(); // no path yet
    fireEvent.change(screen.getByLabelText("Application path"), { target: { value: "C:\\nope" } });
    fireEvent.click(screen.getByRole("button", { name: "Scan Application" }));
    await waitFor(() => expect(screen.getByText(/Path does not exist/)).toBeInTheDocument());
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === "POST")!;
    expect(JSON.parse(post[1]!.body as string)).toEqual({ name: "OpenTelemetry Astronomy Shop", path: "C:\\nope" });
  });
});

function incidentDetail(withApplication: boolean): IncidentDetail {
  const incident = {
    incident_id: "INC-AC837A74", status: "open", title: "PAYMENT_REQUEST_FAILED on payment", service: "payment", environment: null,
    error_type: "PAYMENT_REQUEST_FAILED", status_code: null, endpoint_family: "(none)", affected_endpoints: [], occurrences: 50,
    first_seen: "2026-10-07T18:38:23Z", last_seen: "2026-10-07T18:48:26Z", duration_seconds: 600, severity: "WARNING", levels: { WARNING: 50 },
    status_codes: {}, hosts: [], distinct_clients: 0, peak_per_minute: 17, workflow: "Payment", workflow_criticality: "CRITICAL",
    workflow_reason: "", priority: "P1", priority_score: 86.8, priority_reasons: [], priority_factors: [],
    analysis: { state: "done", error: null },
    ...(withApplication ? {
      application: "OpenTelemetry Astronomy Shop", service_criticality: "CRITICAL",
      priority_reason: "payment is a critical service of the shop; the same traces also failed in checkout -> P1 (score 86.8/100).",
      affected_services: ["checkout", "payment"], trace_ids: ["t1"],
      correlated_incidents: [{ incident_id: "INC-22222222", service: "checkout", shared_traces: 4, relation: "dependent" }],
    } : {}),
  };
  return {
    incident,
    evidence: {
      facts: [], statistics: {}, timeline: { per_minute: [], peak_per_minute: 17, duration_seconds: 600 }, baseline: {},
      related_errors: {} as never, representative_logs: [], runbook: { available: false, runbook_id: null },
      data_limits: { log_lines_in_incident: 50, log_lines_shared_with_ai: 10, not_in_logs: [] },
      application_context: withApplication ? {
        application: "Shop", service: "payment", purpose: "Processes payments.", criticality: "CRITICAL", criticality_source: "compose.yaml",
        depends_on: ["flagd"], depended_on_by: ["checkout"], apis: [], documentation: [], documented_failure_scenarios: [],
      } : null,
    },
    investigation: null,
    recommendations: null,
    resolution: withApplication ? {
      incident_id: "INC-AC837A74", decision: "HUMAN_APPROVAL", decision_reason: "Recommended rollback.", state: "proposed",
      next_step: "A person approves or rejects the action.", based_on_investigation: true, attempts: [], verifications: [], failed_options: [],
      recommended_option_id: "rollback_configuration:payment",
      options: [
        { option_id: "rollback_configuration:payment", action: "rollback_configuration", target_service: "payment", description: "Roll back the config of payment",
          risk: "MEDIUM", impact: "Reverts payment.", blast_radius: "payment and 1 dependent service(s): checkout", blast_radius_level: "LOW",
          reversibility: "REVERSIBLE", confidence: 0.64, execution_mode: "HUMAN_APPROVAL", rationale: "" },
        { option_id: "scale_service:payment", action: "scale_service", target_service: "payment", description: "Scale out payment",
          risk: "LOW", impact: "Adds capacity.", blast_radius: "payment", blast_radius_level: "LOW",
          reversibility: "REVERSIBLE", confidence: 0.2, execution_mode: "HUMAN_TAKEOVER", rationale: "" },
      ],
    } : null,
  } as IncidentDetail;
}

describe("incident application context and resolution", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("explains in plain language why the incident matters and what it depends on", () => {
    const onOpen = vi.fn();
    render(<ApplicationContextCard detail={incidentDetail(true)} onOpen={onOpen} />);
    expect(screen.getByText(/payment is a critical service of the shop/)).toBeInTheDocument();
    expect(screen.getByText("Processes payments.")).toBeInTheDocument();
    expect(screen.getByText("flagd")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "checkout" }));
    expect(onOpen).toHaveBeenCalledWith("INC-22222222");
  });

  it("renders nothing for incidents without application context", () => {
    const { container } = render(<ApplicationContextCard detail={incidentDetail(false)} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("lists options with risk, blast radius, reversibility, mode and the AI recommendation; approve calls the guarded API", async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => new Response(JSON.stringify({})));
    vi.stubGlobal("fetch", fetchMock);
    const onChanged = vi.fn();
    render(<ResolutionCard detail={incidentDetail(true)} onChanged={onChanged} />);
    expect(screen.getByText("AI recommendation")).toBeInTheDocument();
    expect(screen.getByText("MEDIUM risk")).toBeInTheDocument();
    expect(screen.getAllByText("reversible").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Needs approval").length).toBeGreaterThan(0);
    expect(screen.getAllByText("A person must act").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Verify recovery" })).toBeDisabled(); // nothing executed yet
    expect(screen.getByRole("button", { name: "Approve recommended action" })).toBeDisabled(); // no approver identity yet
    fireEvent.change(screen.getByLabelText("Approver"), { target: { value: "alice@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Approve recommended action" }));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/incidents/INC-AC837A74/resolution/execute");
    expect(JSON.parse(init!.body as string)).toMatchObject({ option_id: "rollback_configuration:payment", approved_by: "alice@example.com" });
  });

  it("disables approval when a person must act, and shows verification outcomes", () => {
    const d = incidentDetail(true);
    d.resolution = {
      ...d.resolution!, decision: "HUMAN_TAKEOVER", state: "human_takeover", recommended_option_id: null,
      attempts: [{ attempt_id: "a1", option_id: "x", mode: "simulated", status: "executed", approved_by: "alice", detail: "Simulated: restart", executed_at: "2026-10-07T18:00:00Z" }],
      verifications: [{ attempt_id: "a1", status: "FAILED", reason: "not recovered", checked_at: "2026-10-07T18:02:00Z" }],
    };
    render(<ResolutionCard detail={d} onChanged={() => {}} />);
    expect(screen.getByRole("button", { name: "Approve recommended action" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Take over" })).toBeDisabled();
    expect(screen.getByText("FAILED")).toBeInTheDocument();
  });
});

describe("knowledge page", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("answers a question from the backend and keeps the answer on screen", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ answer: "payment depends on: flagd.", facts: [] }))));
    render(<KnowledgePage />);
    fireEvent.change(screen.getByLabelText("Ask about the application"), { target: { value: "What does payment depend on?" } });
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    await waitFor(() => expect(screen.getByText("payment depends on: flagd.")).toBeInTheDocument());
  });
});

describe("lifecycle reflects the persisted resolution", () => {
  it("shows remediation, verification and closure from the resolution record instead of 'not enabled'", async () => {
    const { lifecycleOfDetail } = await import("../lib/lifecycle");
    const d = incidentDetail(true);
    const steps = (r: IncidentDetail["resolution"]) => Object.fromEntries(lifecycleOfDetail(d.incident, null, null, r ?? null).steps.map((s) => [s.key, s]));
    expect(steps(null).remediate.note).toBe("Not enabled in this POC");
    const proposed = steps(d.resolution);
    expect(proposed.remediate.state).toBe("pending");
    const attempt = { attempt_id: "a1", option_id: "x", mode: "simulated", status: "executed", approved_by: "alice", detail: "d", executed_at: "2026-10-07T18:00:00Z" };
    const executed = steps({ ...d.resolution!, state: "executed", attempts: [attempt] });
    expect(executed.remediate.note).toContain("1 guarded action");
    expect(executed.verify.state).toBe("active");
    const resolved = steps({ ...d.resolution!, state: "resolved", attempts: [attempt],
      verifications: [{ attempt_id: "a1", status: "SUCCESS", reason: "ok", checked_at: "2026-10-07T18:02:00Z" }] });
    expect(resolved.close.state).toBe("done");
    expect(resolved.verify.note).toBe("Latest: SUCCESS");
    expect(lifecycleOfDetail(d.incident, null, null, { ...d.resolution!, state: "human_takeover" }).aiStage).toBe("Human takeover");
  });
});
