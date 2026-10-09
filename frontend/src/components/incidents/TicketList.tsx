import { useEffect, useMemo, useState } from "react";
import { api, type IncidentQuery } from "../../api";
import type { IncidentList } from "../../types";
import { INCIDENT_HEADERS, IncidentRows, type ListFilter } from "../dashboard/Dashboard";

const PAGE = 25;
const PRIORITIES = ["P1", "P2", "P3", "P4"];

const STATUS_FILTERS: Record<string, string> = {
  open: "Awaiting AI",
  investigated: "RCA identified, awaiting recommendations",
  recommendations_ready: "Human decision needed",
};

/** Every incident detected from the logs, highest priority first. */
export function TicketList({ onOpen, refreshKey, initialFilter }: {
  onOpen: (id: string) => void; refreshKey: number; initialFilter?: ListFilter;
}) {
  const [query, setQuery] = useState<IncidentQuery>({ ...initialFilter });
  const [search, setSearch] = useState("");
  const [all, setAll] = useState<IncidentList | null>(null); // unfiltered, for the filter options and counts
  const [data, setData] = useState<IncidentList | null>(null);
  const [page, setPage] = useState(0);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => setQuery((q) => ({ ...q, priority: initialFilter?.priority, status: initialFilter?.status })),
    [initialFilter?.priority, initialFilter?.status]);

  useEffect(() => {
    api.incidents().then(setAll).catch((e) => setError(String(e.message ?? e)));
  }, [refreshKey]);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    api.incidents(query).then((d) => { if (!cancelled) { setData(d); setPage(0); } })
      .catch((e) => setError(String(e.message ?? e)));
    return () => { cancelled = true; };
  }, [query, refreshKey]);

  // Debounced free-text search.
  useEffect(() => {
    const t = window.setTimeout(() => setQuery((q) => ({ ...q, q: search || undefined })), 300);
    return () => window.clearTimeout(t);
  }, [search]);

  const set = (patch: IncidentQuery) => setQuery((q) => ({ ...q, ...patch }));
  const options = useMemo(() => {
    const rows = all?.incidents ?? [];
    return {
      workflows: [...new Set(rows.map((i) => i.workflow))].sort(),
      services: [...new Set(rows.map((i) => i.service))].sort(),
      byPriority: Object.fromEntries(PRIORITIES.map((p) => [p, rows.filter((i) => i.priority === p).length])),
    };
  }, [all]);

  const total = data?.total ?? 0;
  const rows = data?.incidents.slice(page * PAGE, (page + 1) * PAGE) ?? [];

  return (
    <div className="stack">
      <section className="card">
        <div className="list-head">
          <div>
            <h2>Incidents</h2>
            <p className="muted small">
              Every row is one incident case. Detected and triaged by rules (same service, error type, environment and
              endpoint family within a time window); investigated and given recommendations by the AI agents.
            </p>
          </div>
        </div>
        {all && (
          <div className="chips">
            <button type="button" className={`chip ${!query.priority ? "chip-active" : ""}`} onClick={() => set({ priority: undefined })}>
              All <strong>{all.total}</strong>
            </button>
            {PRIORITIES.map((p) => (
              <button key={p} type="button" className={`chip ${query.priority === p ? "chip-active" : ""}`} onClick={() => set({ priority: p })}>
                {p} <strong>{options.byPriority[p]}</strong>
              </button>
            ))}
          </div>
        )}
      </section>

      <section className="card">
        <div className="filters">
          <input type="search" placeholder="Search incident, error type or service" value={search}
                 onChange={(e) => setSearch(e.target.value)} aria-label="Search incidents" />
          <select value={query.service ?? ""} onChange={(e) => set({ service: e.target.value || undefined })} aria-label="Service">
            <option value="">All services</option>
            {options.services.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
          <select value={query.workflow ?? ""} onChange={(e) => set({ workflow: e.target.value || undefined })} aria-label="Workflow">
            <option value="">All workflows</option>
            {options.workflows.map((w) => <option key={w} value={w}>{w}</option>)}
          </select>
          <select value={query.status ?? ""} onChange={(e) => set({ status: e.target.value || undefined })} aria-label="Lifecycle stage">
            <option value="">All lifecycle stages</option>
            {Object.entries(STATUS_FILTERS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </div>

        {error && <div className="alert alert-danger">{error}</div>}
        {data && total === 0 && <p className="muted">No incidents match. Read the application telemetry from the dashboard if you have not yet.</p>}
        {data && total > 0 && (
          <>
            <div className="table-wrap">
              <table className="table tickets">
                <thead>{INCIDENT_HEADERS}</thead>
                <tbody><IncidentRows rows={rows} onOpen={onOpen} /></tbody>
              </table>
            </div>
            <div className="pager">
              <span className="muted small">Showing {page * PAGE + 1}–{Math.min((page + 1) * PAGE, total)} of {total}</span>
              <button type="button" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</button>
              <button type="button" disabled={(page + 1) * PAGE >= total} onClick={() => setPage(page + 1)}>Next</button>
            </div>
          </>
        )}
      </section>
    </div>
  );
}
