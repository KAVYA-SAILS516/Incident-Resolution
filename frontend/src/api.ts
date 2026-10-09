// Thin client for the backend API. The browser never talks to Vertex AI directly.
import type { ActivityEvent, CaseView, Communication, ApplicationView, ChatReply, ChatTurn, Dashboard, Health, IncidentDetail, IncidentList, Investigation, Recommendations, Resolution } from "./types";

const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, init);
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* keep statusText */
    }
    throw new ApiError(response.status, detail);
  }
  return response.json() as Promise<T>;
}

export interface IncidentQuery {
  q?: string;
  priority?: string;
  workflow?: string;
  service?: string;
  status?: string;
}

export const api = {
  health: () => request<Health>("/health"),
  dashboard: () => request<Dashboard>("/api/dashboard/summary"),
  incidents: (query: IncidentQuery = {}) => {
    const params = new URLSearchParams();
    Object.entries(query).forEach(([k, v]) => v !== undefined && v !== "" && params.set(k, String(v)));
    return request<IncidentList>(`/api/incidents?${params}`);
  },
  incident: (id: string) => request<IncidentDetail>(`/api/incidents/${id}`),
  caseOf: (id: string) => request<CaseView>(`/api/incidents/${id}/case`),
  communication: (id: string) => request<Communication>(`/api/incidents/${id}/communication`),
  investigate: (id: string) => request<Investigation>(`/api/incidents/${id}/investigate`, { method: "POST" }),
  recommend: (id: string) => request<Recommendations>(`/api/incidents/${id}/recommendations`, { method: "POST" }),
  applications: () => request<ApplicationView>("/api/applications"),
  /** Read-only scan of a local application directory. */
  scanApplication: (name: string, path: string) =>
    request<ApplicationView>("/api/applications/scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, path }),
    }),
  /** Pull recent OpenTelemetry logs from the configured backend and run detection over them. */
  pullTelemetry: () => request<{ pulled: number; metric_anomaly_events?: number; metrics_error?: string | null; incidents_detected: number }>("/api/telemetry/pull", { method: "POST" }),
  askKnowledge: (question: string) =>
    request<{ answer: string; facts: string[] }>("/api/knowledge/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    }),
  activity: () => request<{ total: number; events: ActivityEvent[] }>("/api/activity"),
  approve: (id: string, optionId: string, approvedBy = "operator") =>
    request<Resolution>(`/api/incidents/${id}/resolution/execute`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ option_id: optionId, approved_by: approvedBy }),
    }),
  takeOver: (id: string) => request<Resolution>(`/api/incidents/${id}/resolution/takeover`, { method: "POST" }),
  verify: (id: string) =>
    request<{ status: "SUCCESS" | "FAILED" | "UNKNOWN"; reason: string }>(`/api/incidents/${id}/resolution/verify`, { method: "POST" }),
  /** Dashboard-wide Chat Agent. `history` is whatever the browser tab is holding; nothing is persisted. */
  chat: (message: string, history: ChatTurn[] = []) =>
    request<ChatReply>("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, history }),
    }),
};
