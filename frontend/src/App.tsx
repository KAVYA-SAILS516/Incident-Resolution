import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import { ActivityPage } from "./components/activity/ActivityPage";
import { ApplicationContextCard } from "./components/incident/ApplicationContextCard";
import { ApplicationsPage } from "./components/applications/ApplicationsPage";
import { KnowledgePage } from "./components/knowledge/KnowledgePage";
import { CaseCard } from "./components/incident/CaseCard";
import { ResolutionCard } from "./components/incident/ResolutionCard";
import { Badge, criticalityTone, priorityTone } from "./components/shared/common";
import { ChatWidget } from "./components/chat/ChatWidget";
import { Dashboard, type ListFilter } from "./components/dashboard/Dashboard";
import { EvidenceModal } from "./components/shared/EvidenceModal";
import { Header, type Tab } from "./components/layout/Header";
import { IncidentOverview } from "./components/incident/IncidentOverview";
import { DecisionSafetyCard } from "./components/incident/DecisionSafetyCard";
import { IncidentSummary } from "./components/incident/IncidentSummary";
import { IncidentTimeline } from "./components/incident/IncidentTimeline";
import { InvestigationCard } from "./components/incident/InvestigationCard";
import { LifecycleStepper } from "./components/incident/LifecycleStepper";
import { PostDecisionStages } from "./components/incident/PostDecisionStages";
import { RecommendationsCard } from "./components/incident/RecommendationsCard";
import { TicketList } from "./components/incidents/TicketList";
import { lifecycleOfDetail } from "./lib/lifecycle";
import { applyTheme, loadTheme, type ThemeChoice } from "./lib/theme";
import type { Dashboard as DashboardData, Health, IncidentDetail } from "./types";

const IN_FLIGHT = new Set(["queued", "investigating", "recommending"]);
const POLL_MS = 3000;

export default function App() {
  // Deep link: ?incident=INC-XXXXXXXX opens that incident directly.
  const linkedId = new URLSearchParams(window.location.search).get("incident");
  const [tab, setTab] = useState<Tab>(linkedId ? "incidents" : "dashboard");
  const [theme, setTheme] = useState<ThemeChoice>(loadTheme);
  const [health, setHealth] = useState<Health | null>(null);
  const [dashboard, setDashboard] = useState<DashboardData | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(linkedId);
  const [detail, setDetail] = useState<IncidentDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [ingesting, setIngesting] = useState(false);
  const [investigatingMore, setInvestigatingMore] = useState(false);
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const [listKey, setListKey] = useState(0);
  const [listFilter, setListFilter] = useState<ListFilter | undefined>(undefined);
  const [selectedOption, setSelectedOption] = useState(0);

  const fail = (e: unknown) => setError(e instanceof Error ? e.message : String(e));

  const chooseTheme = (choice: ThemeChoice) => {
    applyTheme(choice);
    setTheme(choice);
  };

  const loadDashboard = useCallback(() => api.dashboard().then(setDashboard).catch(fail), []);

  const refresh = useCallback(() => {
    api.health().then(setHealth).catch(() => setHealth(null));
    loadDashboard();
    setListKey((k) => k + 1);
  }, [loadDashboard]);

  useEffect(refresh, [refresh]);

  const loadDetail = useCallback((id: string) => api.incident(id).then(setDetail).catch(fail), []);

  useEffect(() => {
    if (selectedId) loadDetail(selectedId);
  }, [selectedId, loadDetail]);

  // Which recommendation is under review in Decision & safety. Reset to the top one whenever a different
  // incident is opened, or this incident's recommendations are (re)generated.
  useEffect(() => setSelectedOption(0), [selectedId, detail?.recommendations?.created_at]);

  // The incident-resolution workflow runs independently in the backend (see backend/app/agents/
  // auto_analysis.py): opening this page never starts it. While the backend reports work in progress on
  // this incident, poll to reflect that - this is reading state, not triggering it.
  useEffect(() => {
    if (!selectedId || !IN_FLIGHT.has(detail?.incident.analysis.state ?? "")) return;
    const timer = window.setInterval(() => loadDetail(selectedId), POLL_MS);
    return () => window.clearInterval(timer);
  }, [selectedId, detail?.incident.analysis.state, loadDetail]);

  // Likewise for the dashboard: poll while the background workflow has anything queued or running.
  useEffect(() => {
    const q = dashboard?.agents.queue;
    if (!q || q.queued + q.investigating + q.recommending === 0) return;
    const timer = window.setInterval(loadDashboard, POLL_MS);
    return () => window.clearInterval(timer);
  }, [dashboard?.agents.queue, loadDashboard]);

  // The backend now reads the application's telemetry by itself (TELEMETRY_POLL_SECONDS), so incidents appear without a
  // click. Reload the dashboard and list periodically while one of them is on screen.
  useEffect(() => {
    if (selectedId || (tab !== "dashboard" && tab !== "incidents")) return;
    const timer = window.setInterval(() => { loadDashboard(); setListKey((k) => k + 1); }, 20000);
    return () => window.clearInterval(timer);
  }, [tab, selectedId, loadDashboard]);

  const open = (id: string) => {
    setDetail(null);
    setSelectedId(id);
    setTab("incidents");
  };

  const back = () => {
    setSelectedId(null);
    setDetail(null);
    setListKey((k) => k + 1);
  };

  const showList = (filter?: ListFilter) => {
    setListFilter(filter);
    setSelectedId(null);
    setTab("incidents");
  };

  const ingest = async () => {
    setError(null);
    setNotice(null);
    setIngesting(true);
    try {
      const s = await api.pullTelemetry();
      setNotice(`Read ${s.pulled} OpenTelemetry log events${s.metric_anomaly_events ? ` and ${s.metric_anomaly_events} Prometheus metric anomalies` : ""}: ${s.incidents_detected} incidents detected. The background workflow is now analysing them.${s.metrics_error ? ` (Metrics unavailable: ${s.metrics_error})` : ""}`);
      refresh();
    } catch (e) {
      fail(e);
    } finally {
      setIngesting(false);
    }
  };

  // "Investigate more": an explicit, human-initiated re-run (Decision & safety), not the background
  // workflow and not triggered by opening the page.
  const investigateMore = async (id: string) => {
    setError(null);
    setInvestigatingMore(true);
    try {
      await api.investigate(id);
      await api.recommend(id);
    } catch (e) {
      fail(e);
    } finally {
      setInvestigatingMore(false);
      await loadDetail(id);
      loadDashboard();
    }
  };

  const inc = detail?.incident;
  const lc = detail ? lifecycleOfDetail(detail.incident, detail.investigation, detail.recommendations, detail.resolution ?? null) : null;

  return (
    <div className="app">
      <Header tab={tab} onTab={(t) => { setTab(t); if (t === "incidents") setSelectedId(null); }}
              health={health} theme={theme} onTheme={chooseTheme} />
      <main>
        {error && <div className="alert alert-danger" role="alert">{error} <button type="button" className="link-button" onClick={() => setError(null)}>Dismiss</button></div>}
        {notice && <div className="working neutral" role="status">{notice} <button type="button" className="link-button" onClick={() => setNotice(null)}>Dismiss</button></div>}

        {tab === "dashboard" && (
          <Dashboard data={dashboard} onIngest={ingest} ingesting={ingesting} onOpen={open} onShowList={showList} />
        )}

        {tab === "incidents" && !selectedId && (
          <TicketList onOpen={open} refreshKey={listKey} initialFilter={listFilter} />
        )}

        {tab === "incidents" && selectedId && (
          <div className="stack">
            <button type="button" className="link-button back" onClick={back}>← All incidents</button>
            {!detail && <p className="muted">Loading {selectedId}…</p>}
            {detail && inc && (
              <>
                <section className="card ticket-head">
                  <div>
                    <span className="field-label">Incident resolution workspace</span>
                    <div className="row" style={{ flexWrap: "wrap" }}>
                      <h2 className="inc-id">{inc.incident_id}</h2>
                      <Badge tone={priorityTone(inc.priority)}>{inc.priority}</Badge>
                      <Badge tone={criticalityTone(inc.service_criticality ?? inc.workflow_criticality)}>{inc.service_criticality ?? inc.workflow_criticality} business criticality</Badge>
                      <Badge tone={lc!.stageTone}>{lc!.aiStage}</Badge>
                      <Badge tone={lc!.statusTone}>{lc!.status}</Badge>
                    </div>
                    <p className="lead">{inc.title}</p>
                    <div className="ws-head-meta">
                      <div><span>Started </span><strong>{inc.first_seen.replace("T", " ").replace("Z", "")} (log time)</strong></div>
                      <div><span>Duration </span><strong>{Math.max(1, Math.round(inc.duration_seconds / 60))} min</strong></div>
                      {inc.application && <div><span>Application </span><strong>{inc.application}</strong></div>}
                      <div><span>Workflow </span><strong>{inc.workflow}</strong></div>
                      <div><span>Service </span><strong>{inc.service}</strong></div>
                      <div><span>Environment </span><strong>{inc.environment ?? "—"}</strong></div>
                    </div>
                  </div>
                </section>
                <IncidentSummary detail={detail} onViewEvidence={() => setEvidenceOpen(true)} />
                <ApplicationContextCard detail={detail} onOpen={open} />
                <LifecycleStepper lifecycle={lc!} />
                <InvestigationCard detail={detail} />
                <RecommendationsCard detail={detail} selectedIndex={selectedOption} onSelect={setSelectedOption} />
                {inc.application ? (
                  <>
                    <ResolutionCard detail={detail} onChanged={() => { loadDetail(inc.incident_id); loadDashboard(); }} />
                    <div className="button-row">
                      <button type="button" disabled={investigatingMore} onClick={() => investigateMore(inc.incident_id)}>
                        {investigatingMore ? "Investigating…" : "Investigate more"}
                      </button>
                    </div>
                    <CaseCard incidentId={inc.incident_id} refreshKey={`${detail.resolution?.state}-${detail.resolution?.attempts.length}-${detail.resolution?.verifications.length}`} />
                    <IncidentTimeline detail={detail} ingestedAt={dashboard?.ingest?.ingested_at ?? null} />
                  </>
                ) : (
                  <>
                    <DecisionSafetyCard detail={detail}
                                        selected={detail.recommendations?.recommendations[selectedOption] ?? detail.recommendations?.recommendations[0] ?? null}
                                        busy={investigatingMore} onInvestigateMore={() => investigateMore(inc.incident_id)} />
                    <div className="two-col">
                      <IncidentTimeline detail={detail} ingestedAt={dashboard?.ingest?.ingested_at ?? null} />
                      <PostDecisionStages detail={detail} />
                    </div>
                  </>
                )}
                <details className="card">
                  <summary><strong>Evidence & triage details</strong> <span className="muted small">(facts, priority factors, related errors)</span></summary>
                  <div className="stack" style={{ marginTop: "0.8rem" }}>
                    <IncidentOverview detail={detail} />
                  </div>
                </details>
              </>
            )}
          </div>
        )}

        {tab === "applications" && <ApplicationsPage onScanned={refresh} />}
        {tab === "activity" && <ActivityPage onOpen={open} />}
        {tab === "knowledge" && <KnowledgePage />}
      </main>
      {evidenceOpen && detail && <EvidenceModal detail={detail} onClose={() => setEvidenceOpen(false)} />}
      <ChatWidget onOpenIncident={open} />
    </div>
  );
}
