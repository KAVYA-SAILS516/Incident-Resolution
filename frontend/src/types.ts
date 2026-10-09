// Shapes returned by the backend API (see backend/app/api/).

export type Priority = "P1" | "P2" | "P3" | "P4";
export type IncidentStatus = "open" | "investigated" | "recommendations_ready";

// The background workflow's own state for one incident (backend/app/agents/auto_analysis.py), read-only
// from the UI's point of view: "queued"/"investigating"/"recommending" mean the backend is working on it
// right now, independent of whether anyone has this page open.
export type AnalysisState = "queued" | "investigating" | "recommending" | "done" | "failed" | "disabled";

export interface IncidentAnalysis {
  state: AnalysisState;
  error: string | null;
}

export interface PriorityFactor {
  name: string;
  value: string;
  normalized: number;
  weight: number;
  points: number;
}

export interface Incident {
  incident_id: string;
  status: IncidentStatus;
  title: string;
  service: string;
  environment: string | null;
  error_type: string;
  status_code: number | null;
  endpoint_family: string;
  affected_endpoints: string[];
  occurrences: number;
  first_seen: string;
  last_seen: string;
  duration_seconds: number;
  severity: string;
  levels: Record<string, number>;
  status_codes: Record<string, number>;
  hosts: string[];
  distinct_clients: number;
  peak_per_minute: number;
  workflow: string;
  workflow_criticality: string;
  workflow_reason: string;
  priority: Priority;
  priority_score: number;
  priority_reasons: string[];
  priority_factors: PriorityFactor[];
  priority_reason?: string;
  // Application context (set when an application has been scanned and the service is known).
  application?: string | null;
  service_criticality?: string | null;
  affected_services?: string[];
  trace_ids?: string[];
  correlated_incidents?: CorrelatedIncident[];
  analysis: IncidentAnalysis;
}

export interface CorrelatedIncident {
  incident_id: string;
  service: string;
  shared_traces: number;
  relation: "dependency" | "dependent" | "same_trace";
  basis?: "trace" | "time+dependency";
}

export interface IncidentList {
  total: number;
  incidents: Incident[];
}

export interface LogLine {
  log_id: string;
  timestamp: string;
  level: string;
  service: string;
  host: string | null;
  method: string | null;
  endpoint: string | null;
  status_code: number | null;
  response_time_ms: number | null;
  error_type: string | null;
  message: string | null;
  client_id: string | null;
  request_id: string | null;
}

export interface Evidence {
  facts: string[];
  statistics: Record<string, any>;
  timeline: { per_minute: { minute: string; count: number }[]; peak_per_minute: number; duration_seconds: number };
  baseline: Record<string, number>;
  related_errors: {
    window: { start: string; end: string };
    cross_service_spike: {
      method: string;
      spike_minutes_in_window: string[];
      incident_events_inside_spike_minutes: number;
      incident_events_outside_spike_minutes: number;
    };
    other_abnormal_logs_in_window: number;
    services_with_errors_in_window: string[];
    by_service_and_type: { service: string; error_type: string; count: number }[];
    overlapping_incidents: { incident_id: string; service: string; error_type: string; priority: Priority }[];
  };
  representative_logs: LogLine[];
  runbook: { available: boolean; runbook_id: string | null };
  data_limits: { log_lines_in_incident: number; log_lines_shared_with_ai: number; not_in_logs: string[] };
  application_context?: ServiceContext | null;
  correlation?: { trace_ids: string[]; affected_services: string[]; correlated_incidents: CorrelatedIncident[]; dependency_contexts: ServiceContext[] };
  telemetry?: {
    traces: { trace_id: string; span_count: number; services: string[]; error_spans: { service: string | null; operation: string; duration_ms: number }[] }[];
    service_metrics: { window_seconds: number; requests: number; errors: number; error_ratio: number } | null;
  };
}

export interface ServiceContext {
  application: string;
  service: string;
  purpose: string;
  criticality: string;
  criticality_source: string | null;
  depends_on: string[];
  depended_on_by: string[];
  apis: string[];
  documentation: string[];
  documented_failure_scenarios: { name: string; description: string | null; default_variant: string | null }[];
}

export interface EvidenceItem {
  statement: string;
  kind: "FACT" | "AI_INFERENCE";
  source: string;
}

export interface Investigation {
  incident_id: string;
  root_cause: string;
  confidence: number;
  evidence: EvidenceItem[];
  analysis: string;
  unknowns: string[];
  insufficient_evidence: boolean;
  model: string;
  provider?: string;
  tools_called: string[];
  based_on_occurrences: number;
  duration_ms: number;
  created_at: string;
}

export interface Recommendation {
  title: string;
  description: string;
  reason: string;
  risk: "LOW" | "MEDIUM" | "HIGH";
  source: "runbook" | "AI-generated";
}

export interface Recommendations {
  incident_id: string;
  recommendations: Recommendation[];
  model: string;
  runbook_id: string | null;
  tools_called: string[];
  validation_notes: string[];
  duration_ms: number;
  created_at: string;
}

export interface IncidentDetail {
  incident: Incident;
  evidence: Evidence;
  investigation: Investigation | null;
  recommendations: Recommendations | null;
  resolution?: Resolution | null;
}

export type ExecutionMode = "AUTO_EXECUTE" | "HUMAN_APPROVAL" | "HUMAN_TAKEOVER";

export interface ResolutionOption {
  option_id: string;
  title?: string;
  prerequisites?: string[];
  expected_outcome?: string;
  action: string;
  target_service: string;
  description: string;
  risk: "LOW" | "MEDIUM" | "HIGH";
  impact: string;
  blast_radius: string;
  blast_radius_level: "LOW" | "MEDIUM" | "HIGH";
  reversibility: "REVERSIBLE" | "PARTIAL" | "IRREVERSIBLE";
  confidence: number;
  execution_mode: ExecutionMode;
  rationale: string;
}

export interface Resolution {
  incident_id: string;
  options: ResolutionOption[];
  recommended_option_id: string | null;
  decision: ExecutionMode;
  decision_reason: string;
  state: "proposed" | "executed" | "resolved" | "human_takeover";
  next_step: string;
  based_on_investigation: boolean;
  attempts: { attempt_id: string; option_id: string; mode: string; status: string; approved_by: string | null; detail: string; executed_at: string }[];
  verifications: { attempt_id: string; status: "SUCCESS" | "FAILED" | "UNKNOWN"; reason: string; checked_at: string }[];
  failed_options: string[];
  reasoned_by?: "policy" | "ai+policy";
  ai_reason?: string | null;
  validation_notes?: string[];
}

export interface ServiceInfo {
  name: string;
  kind: "application" | "infrastructure";
  purpose: string | null;
  criticality: string | null;
  dependencies: string[];
  dependents: string[];
  apis: string[];
  failure_scenarios: string[];
}

export interface ApplicationView {
  scanned: boolean;
  knowledge: {
    application_name: string;
    application_path: string;
    summary: string | null;
    services: ServiceInfo[];
    dependencies: { from: string; to: string }[];
    apis: { name: string }[];
    documentation: { path: string; title: string | null }[];
    telemetry: { signals: Record<string, { receivers: string[]; exporters: string[] }>; collector_configs: string[] };
    failure_scenarios: { name: string; service: string | null; description: string | null }[];
    scan_warnings: string[];
    files_scanned: number;
    last_scanned_at: string;
  } | null;
  telemetry_runtime: { enabled: boolean; collector_endpoint: string | null; logs: boolean; traces: boolean; metrics: boolean };
  configured_name?: string;
  configured_path?: string | null;
  scan_error?: string | null;
  configuration_issues?: string[];
  observability?: string;
  telemetry_health?: ({ enabled: boolean } & Record<"logs" | "metrics" | "traces", { source: string; configured: boolean; reachable: boolean | null; detail: string | null }>) | null;
  service_health?: { service: string; requests: number; errors: number; error_ratio: number }[];
  policies: { priority: Priority; minimum_score: number }[];
}

export interface CaseView {
  incident_id: string;
  application: string | null;
  final_status: string;
  timeline: { at: string; stage: string; source: string; summary: string }[];
}

export interface Communication {
  subject: string;
  body: string;
  status: string;
  generated_by: string;
}

export interface ActivityEvent {
  at: string;
  incident_id: string;
  stage: string;
  summary: string;
  model: string | null;
}

export interface IngestSummary {
  sources: string[];
  total_lines: number;
  parsed_lines: number;
  failed_lines: number;
  error_logs: number;
  warning_logs: number;
  services: string[];
  environments: string[];
  time_range: { start: string | null; end: string | null };
  incidents_detected: number;
  below_threshold_groups: number;
  failures_sample: { source: string; line_number: number; reason: string; raw_line: string }[];
  ingested_at: string;
}

export interface Dashboard {
  application?: { name: string | null; scanned: boolean; observability: string };
  ingested: boolean;
  ingest: IngestSummary | null;
  incidents: {
    total: number;
    by_priority: Record<Priority, number>;
    by_workflow: Record<string, number>;
    by_criticality: Record<string, number>;
    by_service: Record<string, number>;
    by_error_type: Record<string, number>;
    by_status: Record<string, number>;
  };
  agents: {
    investigated: number; recommendations_ready: number; llm_configured: boolean; auto_analyze: boolean; model: string;
    queue: { queued: number; investigating: number; recommending: number; failed: number };
  };
  top_incidents: Incident[];
  timeline: { bucket_minutes: number; points: { time: string; requests: number; errors?: number; warnings?: number }[] };
  analytics: {
    incident_volume: number;
    priority_distribution: Record<Priority, number>;
    workflow_stage_distribution: Record<"queued" | "investigating" | "recommending" | "done" | "failed" | "disabled", number>;
    mttd: Metric; mttr: Metric; resolution_rate: Metric;
    auto_remediation_count: Metric; human_approval_count: Metric; human_takeover_count: Metric;
    verification_outcomes: Metric;
  };
}

/** A measured value, or `available: false` with the reason when there is no data yet. */
export type Metric = NotEnabled | { available: true; value: number | Record<string, number>; note: string; unit?: string };

/** A metric this POC has no backing data for (no remediation, approval, takeover or verification exists). */
export interface NotEnabled {
  available: false;
  note: string;
}

export interface Health {
  status: string;
  llm_configured: boolean;
  model: string;
  logs_ingested: number;
  incidents: number;
}

// Dashboard-wide Chat Agent (third ADK agent). Conversations are ephemeral: kept only in the browser tab.
export interface ChatTurn {
  role: "user" | "assistant";
  content: string;
}

export interface ChatReply {
  reply: string;
  referenced_incidents: string[];
  model: string;
  tools_called: string[];
  duration_ms: number;
}
