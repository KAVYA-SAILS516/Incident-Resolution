import { lifecycleOfRow } from "../../lib/lifecycle";
import type { Dashboard as DashboardData, Incident } from "../../types";
import { Badge, criticalityTone, priorityTone } from "../shared/common";

const PRIORITIES = ["P1", "P2", "P3", "P4"] as const;

export interface ListFilter {
  priority?: string;
  status?: string;
}

/** Command center: what needs attention now, and what is the AI doing about it? */
export function Dashboard({ data, onIngest, ingesting, onOpen, onShowList }: {
  data: DashboardData | null;
  onIngest: () => void;
  ingesting: boolean;
  onOpen: (id: string) => void;
  onShowList: (filter?: ListFilter) => void;
}) {
  if (!data) return <p className="muted">Loading…</p>;
  const ingest = data.ingest;
  const total = data.incidents.total;
  const recommended = data.incidents.by_status.recommendations_ready ?? 0;

  return (
    <div className="dash">
      <section className="card dash-hero">
        <div className="dash-hero-row">
          <div>
            <h2>Command Center</h2>
            <p className="muted">
              What needs attention now, and what is the AI doing about it?{" "}
              {data.application?.scanned ? <strong>{data.application.name} · observed with OpenTelemetry. </strong> : "No application scanned yet. "}
              {ingest
                ? <>{total} active incidents from {ingest.parsed_lines.toLocaleString()} telemetry events,
                    last read {new Date(ingest.ingested_at).toLocaleString()}.</>
                : "No telemetry read yet."}
            </p>
          </div>
          <button type="button" className="primary big" onClick={onIngest} disabled={ingesting}>
            {ingesting ? "Reading…" : "Read Telemetry"}
          </button>
        </div>
      </section>

      <section aria-label="Incident lifecycle">
        <div className="funnel">
          <FunnelStep label="Detected" value={total} note="rules, from logs" onClick={() => onShowList()} />
          <FunnelStep label="Human decision" value={recommended} note="recommendations to review" tone="f-active"
                      onClick={() => onShowList({ status: "recommendations_ready" })} />
        </div>
        <p className="muted small">
          The Investigation and Resolution Decision agents run as a background workflow, independent of this page:
          every incident is analysed, highest priority first, whether or not anyone is viewing it.
          Remediation is guarded and simulated; recovery is verified from OpenTelemetry telemetry.
        </p>
      </section>

      <section className="card">
          <div className="section-head">
            <h2>Needs attention: highest priority</h2>
            {total > 0 && <button type="button" className="link-button" onClick={() => onShowList()}>View all {total}</button>}
          </div>
          {data.top_incidents.length === 0
            ? <p className="muted">{data.application?.scanned ? <>No active incidents. Click <strong>Read Telemetry</strong> to read the application&apos;s OpenTelemetry logs and detect incidents.</> : <>Scan the application on the <strong>Applications</strong> page first, then read its telemetry.</>}</p>
            : <IncidentTable rows={data.top_incidents} onOpen={onOpen} />}
      </section>

      <div className="side-row">
          <section className="card">
            <div className="section-head"><h2>Priority</h2></div>
            <PriorityBars counts={data.incidents.by_priority} total={total} onShow={(p) => onShowList({ priority: p })} />
            <p className="muted small">Rule-based score (POC heuristic): workflow criticality, severity, error class, volume, hosts and burst rate.</p>
          </section>

          {Object.keys(data.incidents.by_workflow).length > 0 && (
            <section className="card">
              <div className="section-head"><h2>Business workflows</h2></div>
              <ul className="status-list">
                {Object.entries(data.incidents.by_workflow).map(([w, n]) => (
                  <li key={w} className="wf-row"><span>{w}</span><strong>{n}</strong></li>
                ))}
              </ul>
            </section>
          )}

          <section className="card">
            <div className="section-head"><h2>Connected sources & AI</h2></div>
            <ul className="status-list">
              <li>
                <span className={`status-dot ${ingest ? "ok" : "bad"}`} aria-hidden />
                <div>
                  <div className="status-name">Logs · Source: OpenTelemetry</div>
                  <div className="muted small">
                    {ingest
                      ? `${ingest.sources.join(", ")} · ${ingest.failed_lines} unparsed line(s) · ${ingest.error_logs} errors, ${ingest.warning_logs} warnings · ${ingest.below_threshold_groups} small groups below threshold`
                      : "Not read yet"}
                  </div>
                </div>
              </li>
              <li>
                <span className="status-dot ok" aria-hidden />
                <div>
                  <div className="status-name">Runbooks</div>
                  <div className="muted small">Generic simulated runbooks (data/runbooks); none are tied to the monitored application</div>
                </div>
              </li>
              <li>
                <span className={`status-dot ${data.agents.llm_configured && data.agents.auto_analyze ? "ok" : "bad"}`} aria-hidden />
                <div>
                  <div className="status-name">Investigation & Resolution Decision agents (Google ADK)</div>
                  <div className="muted small">
                    {data.agents.model} · background workflow {data.agents.auto_analyze ? "on" : "off"} ·{" "}
                    {data.agents.investigated} investigated · {data.agents.recommendations_ready} with recommendations
                    {(data.agents.queue.queued + data.agents.queue.investigating + data.agents.queue.recommending) > 0 &&
                      ` · ${data.agents.queue.queued} queued, ${data.agents.queue.investigating + data.agents.queue.recommending} running now`}
                    {data.agents.queue.failed > 0 && ` · ${data.agents.queue.failed} failed`}
                  </div>
                </div>
              </li>
              <li>
                <span className="status-dot unknown" aria-hidden />
                <div>
                  <div className="status-name">Metrics, change history, ITSM</div>
                  <div className="muted small">Not connected in this POC</div>
                </div>
              </li>
            </ul>
          </section>
      </div>
    </div>
  );
}

function FunnelStep({ label, value, note, tone = "", onClick }: {
  label: string; value?: number; note: string; tone?: string; onClick?: () => void;
}) {
  const body = (
    <>
      <span className="funnel-label">{label}</span>
      <span className="funnel-value">{value ?? "—"}</span>
      <span className="funnel-note">{note}</span>
    </>
  );
  return onClick
    ? <button type="button" className={`funnel-step ${tone}`} onClick={onClick}>{body}</button>
    : <div className={`funnel-step ${tone}`}>{body}</div>;
}

function PriorityBars({ counts, total, onShow }: { counts: Record<string, number>; total: number; onShow: (p: string) => void }) {
  return (
    <div className="pbars">
      {PRIORITIES.map((k) => {
        const n = counts[k] ?? 0;
        return (
          <div key={k} className="pbar-row">
            <button type="button" className="link-button" onClick={() => onShow(k)} aria-label={`Show ${k} incidents`}>
              <Badge tone={priorityTone(k)}>{k}</Badge>
            </button>
            <span className="pbar"><span className={`pbar-fill pbar-${k}`} style={{ width: `${total ? (100 * n) / total : 0}%` }} /></span>
            <strong>{n}</strong>
          </div>
        );
      })}
    </div>
  );
}

/** Shared incident row layout: ID, workflow, criticality, priority, status, action. */
export function IncidentRows({ rows, onOpen }: { rows: Incident[]; onOpen: (id: string) => void }) {
  return (
    <>
      {rows.map((r) => {
        const lc = lifecycleOfRow(r);
        return (
          <tr key={r.incident_id}>
            <td><span className="inc-id">{r.incident_id}</span><div className="muted small">{r.error_type} · {r.service}</div></td>
            <td className="small hide-sm">{r.workflow}</td>
            <td className="hide-sm"><Badge tone={criticalityTone(r.workflow_criticality)}>{r.workflow_criticality}</Badge></td>
            <td><Badge tone={priorityTone(r.priority)}>{r.priority}</Badge></td>
            <td className="small">{lc.status}</td>
            <td>
              <button type="button" className={lc.needsHuman ? "primary" : ""} onClick={() => onOpen(r.incident_id)}>
                {lc.needsHuman ? "Review" : "View"}
              </button>
            </td>
          </tr>
        );
      })}
    </>
  );
}

export const INCIDENT_HEADERS = (
  <tr>
    <th>Incident</th><th className="hide-sm">Workflow</th><th className="hide-sm">Criticality</th><th>Priority</th>
    <th>Status</th><th>Action</th>
  </tr>
);

function IncidentTable({ rows, onOpen }: { rows: Incident[]; onOpen: (id: string) => void }) {
  return (
    <div className="table-wrap">
      <table className="table dash-table">
        <thead>{INCIDENT_HEADERS}</thead>
        <tbody><IncidentRows rows={rows} onOpen={onOpen} /></tbody>
      </table>
    </div>
  );
}
