import type { IncidentDetail } from "../../types";
import { Card, InfoTip } from "../shared/common";

const logTime = (iso: string) => iso.replace("T", " ").replace("Z", "").slice(0, 19);
const sysTime = (iso: string) => new Date(iso).toLocaleString();

interface Event {
  at: string;
  text: string;
  pending?: boolean;
}

/** Real events only: log timestamps from the incident, system timestamps from ingest and the agents. */
export function IncidentTimeline({ detail, ingestedAt }: { detail: IncidentDetail; ingestedAt: string | null }) {
  const { incident: i, investigation, recommendations } = detail;
  const events: Event[] = [
    { at: `${logTime(i.first_seen)} (log)`, text: `First ${i.error_type} event on ${i.service}` },
    { at: `${logTime(i.last_seen)} (log)`, text: `Last event (${i.occurrences} in total)` },
  ];
  // System events, in the order they actually happened (logs can be re-processed after an investigation).
  const system: { iso: string; text: string }[] = [];
  if (ingestedAt) {
    system.push({ iso: ingestedAt, text: `Logs processed: detected, correlated and triaged as ${i.priority}, ${i.workflow} (${i.workflow_criticality})` });
  }
  if (investigation) {
    system.push({ iso: investigation.created_at,
      text: investigation.insufficient_evidence ? "Investigation finished: RCA inconclusive" : `Root cause identified (${Math.round(investigation.confidence * 100)}% confidence)` });
  }
  if (recommendations) {
    system.push({ iso: recommendations.created_at, text: `${recommendations.recommendations.length} recommendations generated` });
  }
  system.sort((a, b) => a.iso.localeCompare(b.iso)).forEach((e) => events.push({ at: sysTime(e.iso), text: e.text }));
  if (recommendations) events.push({ at: "now", text: "Awaiting a human decision", pending: true });

  return (
    <Card question="What has happened so far?" title="Incident timeline"
          actions={<InfoTip text="Only events recorded by the system. Log times come from the log file; other times are when this system processed the incident." />}>
      <ol className="timeline">
        {events.map((e, n) => (
          <li key={n} className={e.pending ? "t-pending" : ""}>
            <div className="timeline-time">{e.at}</div>
            <div>{e.text}</div>
          </li>
        ))}
      </ol>
    </Card>
  );
}
