import { useEffect, useState } from "react";
import { api } from "../../api";
import type { ApplicationView } from "../../types";
import { Badge, Card, criticalityTone } from "../shared/common";

const DEFAULT_NAME = "OpenTelemetry Astronomy Shop";

/** Application path + the knowledge discovered from it (read-only scan; nothing from the path is executed). */
export function ApplicationsPage({ onScanned }: { onScanned?: () => void }) {
  const [view, setView] = useState<ApplicationView | null>(null);
  const [name, setName] = useState(DEFAULT_NAME);
  const [path, setPath] = useState("");
  const [busy, setBusy] = useState(false);
  const [pulling, setPulling] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    api.applications().then((v) => {
      setView(v);
      if (v.knowledge) {
        setName(v.knowledge.application_name);
        setPath(v.knowledge.application_path);
      } else {
        setName(v.configured_name ?? DEFAULT_NAME);
        setPath(v.configured_path ?? "");
      }
    }).catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  const scan = async () => {
    setError(null);
    setNotice(null);
    setBusy(true);
    try {
      setView(await api.scanApplication(name, path));
      onScanned?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const pull = async () => {
    setError(null);
    setNotice(null);
    setPulling(true);
    try {
      const r = await api.pullTelemetry();
      setNotice(`Read ${r.pulled} telemetry events${r.metric_anomaly_events ? ` and ${r.metric_anomaly_events} metric anomalies` : ""}; ${r.incidents_detected} incidents detected in total.`);
      onScanned?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPulling(false);
    }
  };

  const k = view?.knowledge ?? null;
  const services = k?.services.filter((s) => s.kind === "application") ?? [];
  const runtime = view?.telemetry_runtime;

  return (
    <div className="stack">
      <Card question="Which application is being watched?" title="Application">
        <div className="filters">
          <label className="stack" style={{ gap: "0.2rem", flex: 1 }}>
            <span className="field-label">Application name</span>
            <input value={name} onChange={(e) => setName(e.target.value)} aria-label="Application name" />
          </label>
          <label className="stack" style={{ gap: "0.2rem", flex: 2 }}>
            <span className="field-label">Application path (local directory)</span>
            <input value={path} onChange={(e) => setPath(e.target.value)} placeholder="C:\\incident-data\\astronomy-shop" aria-label="Application path" />
          </label>
        </div>
        <div className="button-row">
          <button type="button" onClick={scan} disabled={busy || !name.trim() || !path.trim()}>
            {busy ? "Scanning…" : k ? "Rescan Application" : "Scan Application"}
          </button>
        </div>
        {error && <div className="alert alert-danger" role="alert">{error}</div>}
        {(view?.configuration_issues ?? []).map((issue) => <div key={issue} className="alert alert-warning small" role="alert">{issue}</div>)}
        {!error && view?.scan_error && <div className="alert alert-danger" role="alert">{view.scan_error}</div>}
        {k && (
          <dl className="facts compact" style={{ marginTop: "0.8rem" }}>
            <div><dt>Status</dt><dd><Badge tone="success">Scanned</Badge></dd></div>
            <div><dt>Path</dt><dd>{k.application_path}</dd></div>
            <div><dt>Last scan</dt><dd>{new Date(k.last_scanned_at).toLocaleString()}</dd></div>
            <div><dt>Files read</dt><dd>{k.files_scanned}</dd></div>
            <div><dt>Observability</dt><dd>{view?.observability ?? "OpenTelemetry"}</dd></div>
          </dl>
        )}
        {k && (
          <dl className="facts compact">
            <div><dt>Application services</dt><dd>{services.length}</dd></div>
            <div><dt>Critical services</dt><dd>{services.filter((s) => s.criticality === "CRITICAL").map((s) => s.name).join(", ") || "none declared"}</dd></div>
            <div><dt>Dependencies</dt><dd>{k.dependencies.length}</dd></div>
            <div><dt>APIs</dt><dd>{k.apis.length}</dd></div>
            <div><dt>Failure scenarios / feature flags</dt><dd>{k.failure_scenarios.length}</dd></div>
            <div><dt>Documents</dt><dd>{k.documentation.length}</dd></div>
          </dl>
        )}
        {k?.summary && <p className="muted small">{k.summary}</p>}
        {k?.scan_warnings.map((w) => <p key={w} className="alert alert-warning small">{w}</p>)}
        {!k && !error && <p className="muted small">Enter the path of the application to scan. The scan only reads files; it never runs anything.</p>}
      </Card>

      {k && (
        <Card question="What does the application consist of?" title={`Services (${services.length})`}>
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Service</th><th>Purpose</th><th>Criticality</th><th>Dependencies</th></tr></thead>
              <tbody>
                {services.map((s) => (
                  <tr key={s.name}>
                    <td><strong>{s.name}</strong></td>
                    <td>{s.purpose ?? <span className="muted">unknown</span>}</td>
                    <td>{s.criticality ? <Badge tone={criticalityTone(s.criticality)}>{s.criticality}</Badge> : <span className="muted">unknown</span>}</td>
                    <td>{s.dependencies.join(", ") || <span className="muted">none found</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {k && (
        <div className="two-col">
          <Card question="What telemetry is there?" title="Telemetry">
            <ul className="plain">
              {(["logs", "metrics", "traces"] as const).map((signal) => {
                const h = view?.telemetry_health?.[signal];
                const up = h?.reachable === true;
                return (
                  <li key={signal} className={up ? "ok" : "missing"}>
                    {up ? "✓" : "✗"} {signal[0].toUpperCase() + signal.slice(1)}
                    <span className="muted small"> · Source: {h?.source ?? signal} · {up ? "connected" : `unavailable${h?.detail ? ` (${h.detail})` : ""}`}</span>
                    {k.telemetry.signals[signal] && <span className="muted small"> · configured in the application via {k.telemetry.signals[signal].exporters.join(", ") || "collector"}</span>}
                  </li>
                );
              })}
            </ul>
            {view?.telemetry_health && ["logs", "metrics", "traces"].some((s) => view.telemetry_health![s as "logs"].reachable !== true) && (
              <div className="alert alert-warning small" role="alert">{k.application_name} telemetry unavailable for at least one signal. Nothing is substituted for it.</div>
            )}
            <p className="muted small">
              Runtime: {runtime?.enabled ? "enabled" : "disabled"} · logs {runtime?.logs ? "configured" : "not configured"} ·
              traces {runtime?.traces ? "configured" : "not configured"} · metrics {runtime?.metrics ? "configured" : "not configured"}
            </p>
            <div className="button-row">
              <button type="button" onClick={pull} disabled={pulling || !runtime?.enabled}
                      title={runtime?.enabled ? "Read recent logs from the telemetry backend" : "Set OTEL_ENABLED=true and OTEL_LOGS_URL on the backend"}>
                {pulling ? "Reading…" : "Read telemetry now"}
              </button>
            </div>
            {notice && <p className="small" role="status">{notice}</p>}
          </Card>
          <Card question="How healthy is each service right now?" title="Service health (Prometheus span metrics, last 5 min)">
            {(view?.service_health ?? []).length === 0
              ? <p className="muted small">Metrics unavailable.</p>
              : (
                <div className="table-wrap" style={{ maxHeight: 260, overflowY: "auto" }}>
                  <table className="table">
                    <thead><tr><th>Service</th><th>Requests</th><th>Failing</th></tr></thead>
                    <tbody>{view!.service_health!.map((h) => (
                      <tr key={h.service}><td>{h.service}</td><td>{h.requests}</td>
                        <td>{(h.error_ratio * 100).toFixed(1)}%</td></tr>))}</tbody>
                  </table>
                </div>
              )}
          </Card>
          <Card question="How urgent is an incident?" title="Policies">
            <table className="table">
              <thead><tr><th>Priority</th><th>Minimum score</th></tr></thead>
              <tbody>{view!.policies.map((p) => <tr key={p.priority}><td><strong>{p.priority}</strong></td><td>{p.minimum_score}</td></tr>)}</tbody>
            </table>
            <p className="muted small">Score = business criticality, the service&apos;s own criticality, severity, error class, volume, blast radius and burst rate.</p>
          </Card>
        </div>
      )}
    </div>
  );
}
