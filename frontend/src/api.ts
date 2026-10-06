// Thin client for the backend API. The browser never talks to Vertex AI directly.
import type { ChatReply, ChatTurn, Dashboard, Health, IncidentDetail, IncidentList, IngestSummary, Investigation, Recommendations } from "./types";

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
  /** Process the log files in data/logs/. (The endpoint also accepts a file upload; the UI does not use it.) */
  ingest: () => request<IngestSummary>("/api/logs/ingest", { method: "POST" }),
  investigate: (id: string) => request<Investigation>(`/api/incidents/${id}/investigate`, { method: "POST" }),
  recommend: (id: string) => request<Recommendations>(`/api/incidents/${id}/recommendations`, { method: "POST" }),
  /** Dashboard-wide Chat Agent. `history` is whatever the browser tab is holding; nothing is persisted. */
  chat: (message: string, history: ChatTurn[] = []) =>
    request<ChatReply>("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, history }),
    }),
};
