import { useEffect, useState } from "react";
import { api } from "../../api";
import type { ActivityEvent } from "../../types";
import { Card } from "../shared/common";

/** What the agents and guarded actions have done (read from persisted records). */
export function ActivityPage({ onOpen }: { onOpen: (incidentId: string) => void }) {
  const [events, setEvents] = useState<ActivityEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.activity().then((r) => setEvents(r.events)).catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  return (
    <Card question="What have the agents done?" title="Agent activity">
      {error && <div className="alert alert-danger" role="alert">{error}</div>}
      {!events && !error && <p className="muted">Loading…</p>}
      {events && events.length === 0 && <p className="muted small">No agent activity yet.</p>}
      {events && events.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Time</th><th>Incident</th><th>Stage</th><th>What happened</th></tr></thead>
            <tbody>
              {events.map((e, n) => (
                <tr key={n}>
                  <td className="nowrap">{new Date(e.at).toLocaleString()}</td>
                  <td>{e.incident_id ? <button type="button" className="link-button" onClick={() => onOpen(e.incident_id)}>{e.incident_id}</button> : "—"}</td>
                  <td>{e.stage}</td>
                  <td>{e.summary}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
