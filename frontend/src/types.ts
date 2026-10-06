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
  analysis: IncidentAnalysis;
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
    mttd: NotEnabled; mttr: NotEnabled; resolution_rate: NotEnabled;
    auto_remediation_count: NotEnabled; human_approval_count: NotEnabled; human_takeover_count: NotEnabled;
    verification_outcomes: NotEnabled;
  };
}

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
