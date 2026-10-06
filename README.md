# AI-Native Incident Resolution POC

**Deterministic Python decides WHAT happened and HOW IMPORTANT it is. Two Google ADK agents reason about WHY it
happened and WHAT COULD BE DONE.** The POC stops at recommendations: nothing is remediated, executed or verified.
A third agent (§12a) is a dashboard-wide chatbot for asking questions about what the first two already found.

```
FastAPI
  → log ingestion (data/logs/*.log, read-only)
  → parsing / normalisation                         services/log_parser.py
  → incident detection and grouping                 services/incident_detector.py
  → workflow classification + criticality           services/workflow_classifier.py, config/workflows.py
  → priority P1-P4 (score + reasons)                services/priority_engine.py, config/priority_rules.py
  → evidence (aggregates + representative logs)     services/evidence_service.py
  → Investigation Agent (Google ADK)                agents/investigation_agent.py
  → Recommendation Agent (Google ADK)                agents/recommendation_agent.py
  → end of POC

Both agents run as a background workflow (agents/auto_analysis.py), independent of the UI: every incident
is analysed automatically, highest priority first, as soon as it exists.

A third, separate agent - the Chat Agent (agents/chat_agent.py) - answers free-form questions about
incidents on demand, from a floating widget. It never investigates, recommends or starts the workflow above.
```

Out of scope by design: remediation, verification, an orchestrator agent, Postgres, Redis, Kafka, a vector DB,
Docker and Celery.

## 1. Project layout

```
backend/
  app/
    main.py                  FastAPI app, CORS, routers, GET /health
    api/                     logs.py, incidents.py, investigation.py, recommendations.py, dashboard.py, chat.py
    agents/                  investigation_agent.py, recommendation_agent.py, runner.py (ADK Runner),
                             auto_analysis.py (background workflow, §12 "The background workflow"),
                             chat_agent.py (third agent, §12a "The Chat Agent")
    services/                log_parser.py, ingestion.py, incident_detector.py, workflow_classifier.py,
                             priority_engine.py, evidence_service.py, dashboard_service.py
    tools/                   log_tools.py, incident_tools.py, runbook_tools.py, chat_tools.py (the agents' ADK tools)
    config/                  settings.py (env), workflows.py, priority_rules.py
    schemas/                 log.py, incident.py, investigation.py, recommendation.py, chat.py (Pydantic)
    state/store.py           local JSON state
  tests/                     pytest suite (see §15)
  .env.example, requirements.txt, pytest.ini
frontend/                    React + Vite + TypeScript UI (talks only to the backend)
data/
  logs/sample_application.log    the raw log (read-only source data)
  logs/uploads/                  files uploaded through the API (new files, never overwritten)
  runbooks/                      SIMULATED runbooks, one per error type in the log
  incidents/, logs/*.json        generated state
```

## 2. Setup

Requires Python 3.11+ and Node 18+.

```bash
python -m venv .venv
.venv/Scripts/pip install -r backend/requirements.txt      # Windows (use .venv/bin/pip elsewhere)
cp backend/.env.example backend/.env                         # then set GOOGLE_CLOUD_PROJECT
gcloud auth application-default login                        # Vertex AI via Application Default Credentials
cd frontend && npm install
```

## 3. Configuration (`backend/.env`)

Only the variables the ADK configuration actually needs are required. There is no API key: Vertex AI
authenticates with Application Default Credentials.

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `GOOGLE_CLOUD_PROJECT` | yes (for the agents) | none | Vertex AI project |
| `GOOGLE_CLOUD_LOCATION` | yes | `us-central1` | Vertex AI region |
| `VERTEX_MODEL` | yes | `gemini-2.5-flash` | Gemini model used by both agents |
| `LLM_TIMEOUT_SECONDS`, `LLM_MAX_ATTEMPTS` | no | 120, 4 | request timeout and retries (429/5xx) |
| `AUTO_ANALYZE` | no | `true` | run the background incident-resolution workflow (§4) |
| `MAX_CONCURRENT_ANALYSES` | no | 4 | incidents analysed in parallel |
| `DETECTION_WINDOW_MINUTES` | no | 10 | grouping time window |
| `DETECTION_MIN_OCCURRENCES` | no | 5 | events needed to open an incident |
| `ENDPOINT_FAMILY_DEPTH` | no | 1 | path segments that define an "endpoint family" |
| `CORS_ORIGINS`, `DATA_DIR` | no | localhost:5173, `../data` | |

Without `GOOGLE_CLOUD_PROJECT` everything deterministic still works; all three agent endpoints return 503.

## 4. Running

```bash
cd backend
../.venv/Scripts/python -m uvicorn app.main:app --reload     # http://127.0.0.1:8000, docs at /docs
cd frontend
npm run dev                                                  # http://localhost:5173 (proxies /api and /health)
```

If the backend runs on another port: `VITE_API_TARGET=http://127.0.0.1:8010 npm run dev`.

**The incident-resolution workflow runs in the backend, independent of the UI.** After `POST /api/logs/ingest`
finishes, and again at startup for anything left unfinished by a previous run, the backend queues every
incident - highest priority first - and runs the Investigation Agent then the Recommendation Agent for each,
up to `MAX_CONCURRENT_ANALYSES` at a time (`app/agents/auto_analysis.py`). This happens whether or not anyone
has the UI open. Click **Process Logs** on the dashboard, then open any incident: the page only ever *displays*
the backend's current state (`incident.analysis.state`: `queued` / `investigating` / `recommending` / `done` /
`failed` / `disabled`), polling while it is in progress - it never starts an agent itself. There are no "run"
buttons. The one exception is **Investigate more** in Decision & safety, an explicit, human-initiated re-run.
`?incident=INC-XXXXXXXX` opens an incident directly.

To add another log file, put it in `data/logs/` and click **Process Logs**, or upload it through the API (there is
no upload button in the UI): `curl -F file=@extra.log http://127.0.0.1:8000/api/logs/ingest`.

The floating chat bubble (bottom-right, every page) is unrelated to all of the above: it is a separate,
explicitly human-initiated action (§12a) that only answers questions - it never starts the workflow.

## 5. API

| Method | Path | What it does |
|---|---|---|
| `POST` | `/api/logs/ingest` | Process every raw log in `data/logs/` (+ optional multipart `file` upload, saved as a new source). Returns `total_lines`, `parsed_lines`, `failed_lines`, `error_logs`, `warning_logs`, `services`, `incidents_detected`, `below_threshold_groups`, `failures_sample`. |
| `GET` | `/api/logs/summary` | Last ingest summary |
| `GET` | `/api/incidents` | Incidents, highest priority first. Filters: `priority`, `workflow`, `service`, `status`, `q`. Each incident carries `analysis: {state, error}`, the background workflow's current state for it |
| `GET` | `/api/incidents/{id}` | Incident + deterministic evidence + investigation + recommendations |
| `POST` | `/api/incidents/{id}/investigate` | Explicit, human-triggered re-run of the Investigation Agent (synchronous, ~10-30 s). The background workflow already runs this automatically for every incident; this is for "Investigate more" |
| `POST` | `/api/incidents/{id}/recommendations` | Explicit re-run of the Recommendation Agent (409 until investigated) |
| `GET` | `/api/dashboard/summary` | Counts, `agents.queue` (background workflow activity), `analytics`, top incidents, 5-minute timeline |
| `POST` | `/api/chat` | Dashboard-wide Chat Agent. Body `{message, history}` (`history` is whatever the client holds - nothing is persisted server-side); returns `{reply, referenced_incidents, model, tools_called, duration_ms}` |
| `GET` | `/health` | Liveness, whether Vertex AI is configured, counts |

Agent errors: 503 when Vertex AI is not configured, 502 when the model fails or returns an invalid result.
GET requests never start an agent; they only report the background workflow's state.

## 6. Source data

`data/logs/sample_application.log`: 5,000 lines, 2026-09-01 09:00:00Z to 11:46:38Z, one request every 2 s, four
services (auth, order, payment, catalog), hosts app-01 to app-04, environment `production-simulated`.
513 abnormal lines: SLOW_REQUEST 209 (WARNING, HTTP 200), AUTHENTICATION_FAILURE 69 (401), INTERNAL_SERVER_ERROR 66
(500), DATABASE_TIMEOUT 63 (503), RATE_LIMIT_EXCEEDED 60 (429), RESOURCE_PRESSURE 46 (503). Baseline is roughly 2
abnormal lines per minute, with two cross-service spikes at 09:30-09:32 and 10:43-10:46 (15-23 per minute).

The file is treated as read-only: it is opened for reading only, and uploads are written as new files under
`data/logs/uploads/` (opened with exclusive-create, so nothing is ever overwritten). It was copied into
`data/logs/` from the original download; the original is unchanged (same SHA-1).

## 7. Parsing

Format: `<ISO-8601 timestamp> key=value key="quoted value" ...`. Output per line: `timestamp` (UTC), `level`
(WARN→WARNING, FATAL→CRITICAL), `service`, `environment`, `host`, `request_id`, `client_id`, `method`, `endpoint`,
`status_code`, `response_time_ms`, `error_type` (`NONE` → null), `message`, `raw_message` (the original line) and
a `log_id` (`file:line`). Malformed lines never stop ingestion. Each one is reported with a reason: no timestamp,
invalid date, a missing `level`/`service`, or a non-numeric number field. Blank lines are skipped and not counted.

## 8. Incident detection and grouping

An entry is abnormal when its level is WARNING/ERROR/CRITICAL, it has an `error_type`, or its status is ≥ 500.
Abnormal entries are grouped by **service + error type + environment + endpoint family**. The endpoint
family is the resource after `/api/vN`, with id-like segments removed, so `/api/v1/orders` and
`/api/v1/orders/status` group together while `/payments` and `/refunds` do not. Within a group, an entry joins
the current episode if it arrives within `DETECTION_WINDOW_MINUTES` of that episode's previous entry. Episodes
with at least `DETECTION_MIN_OCCURRENCES` entries become incidents; smaller ones are counted as
`below_threshold_groups` (background noise). Incident ids are a hash of the key plus `first_seen`, so
re-ingesting gives the same ids and keeps the agent results attached.

On the sample log this gives **21 incidents** from 513 abnormal lines, with 183 small groups below the
threshold.

**Known limitation, stated plainly:** the grouping key required by the spec is per service/error/endpoint,
while the two real events in this log are cross-service spikes. During the spikes, about 50 failures spread
across ~50 different keys, one or two each, so no single incident "is" the spike. Instead, the evidence service
marks spike minutes (§11), and the Investigation Agent sees whether an incident overlaps one. A spike-level
correlation incident would be the natural next step.

## 9. Workflow classification and criticality

`config/workflows.py` lists 20 workflows plus Unknown, each with keywords. Endpoint path tokens (weight 3) and
service-name tokens (weight 2) are matched, including plurals, and the highest score wins.
Criticality: Authentication, Authorization, Integration, Monitoring/Health, Audit/Compliance and
Deployment/Release are HIGH. Transaction, Payment, Checkout/Order and Backup/Recovery are CRITICAL. Search, Data
Retrieval, Data Processing, File/Document, Notification, Scheduling, Administration, Background Jobs and
Communication are MEDIUM. Reporting is LOW. Unknown is UNKNOWN.
In the sample log: `/login` and `/token` → Authentication, `/payments` and `/refunds` → Payment, `/orders*` →
Checkout/Order, `/search` → Search, `/products` → Data Retrieval.

## 10. Priority scoring (a POC heuristic)

`score = 100 × Σ weight × factor`, where each factor is normalised to 0..1. All numbers are in
`config/priority_rules.py`.

| Factor | Weight | Values |
|---|---|---|
| workflow criticality | 0.40 | CRITICAL 5, HIGH 4, MEDIUM 3, LOW 2, UNKNOWN 1 (÷5) |
| severity (highest level) | 0.15 | CRITICAL 1.0, ERROR 0.75, WARNING 0.4 |
| error class | 0.15 | 5xx 1.0, 429 0.5, other 4xx 0.4, 2xx (slow but succeeded) 0.2 |
| volume | 0.15 | ≥50: 1.0, ≥25: 0.75, ≥10: 0.5, ≥5: 0.3 |
| blast radius (hosts) | 0.10 | 4: 1.0, 3: 0.8, 2: 0.5, 1: 0.25 |
| burst rate (peak/min) | 0.05 | ≥6: 1.0, ≥4: 0.7, ≥2: 0.4 |

P1 ≥ 80, P2 ≥ 65, P3 ≥ 50, otherwise P4. Every incident carries its factor table and readable reasons.
Criticality alone moves a score by up to 32 points, so Payment and Reporting incidents with the same other
signals land in different priorities (this is tested). Sample log result: 3 P1 (server-side failures on
Checkout/Order during the spikes), 7 P2, 9 P3, 2 P4.

## 11. Evidence (what the agents see)

Raw logs are never sent to Gemini in bulk. `build_evidence` produces a compact package (<40 KB):
- counts by level, status, host, endpoint, method and client, plus the message variants;
- response-time p50/p95 compared with the service's normal requests;
- the per-minute timeline;
- service and global abnormal rates, both in the incident window and overall;
- related errors in the window (±2 min), counted as log lines, with the overlapping incidents listed separately;
- a **cross-service spike** assessment: a spike minute has ≥ max(10, 5 × median) abnormal lines across all
  services, which on this log finds exactly the two known spikes;
- up to 10 representative lines (first, last, slowest, one per host and per endpoint/status variant);
- runbook availability;
- an explicit list of what is not in the data (metrics, deployments, traces, database-server logs).

## 12. The two Google ADK agents

Both are `google.adk.agents.LlmAgent`s (ADK 2.9.2) with Gemini on Vertex AI (`Gemini(client=genai.Client(vertexai=True, ...))`),
temperature 0.2, a Pydantic `output_schema` and an `output_key`. Each runs once through
`google.adk.runners.Runner` with an `InMemorySessionService` (`agents/runner.py`). Their tools are closures bound
to one incident's evidence, so the model can't read other incidents or pull more raw logs. Because
gemini-2.5-flash can't combine tools with a native output schema, ADK adds its `set_model_response` tool
automatically.

**Investigation Agent.** Tools: `get_incident_details`, `get_log_statistics`, `get_error_timeline`,
`get_related_errors`, `get_representative_logs`. Output: `root_cause`, `confidence`, `evidence[]` (each marked
**FACT** from a tool, with its source, or **AI_INFERENCE**), `analysis`, `unknowns[]` (**UNKNOWN**) and
`insufficient_evidence`. When nothing supports a cause, the root cause is exactly "Insufficient evidence".
Deterministic guardrails:
- Confidence is capped at 0.85, because log-only evidence can't prove a cause.
- It is capped at 0.3 when evidence is insufficient.
- It is capped at 0.4 when no FACT is cited.

**Recommendation Agent.** Tools: `get_incident_details`, `get_investigation_result`, `get_runbook`. Output:
`recommendations[]` with `title`, `description`, `reason`, `risk` (LOW/MEDIUM/HIGH) and `source` ("runbook" or
"AI-generated"). A deterministic provenance check relabels any "runbook" item whose title doesn't match a real
runbook step (fuzzy ratio ≥ 0.75) as AI-generated, and records a note.

Observed live behaviour on this log (gemini-2.5-flash):
- The agents call all their tools.
- They separate facts from inference.
- They use the spike data correctly (for example, "3 of 7 events inside spike minutes" versus "only 1 of 7,
  primarily localized").
- Recommendations reuse runbook steps verbatim.

Confidence tends to sit at the top of its allowed band (0.75-0.8), even when the cause is inferred.
Treat it as the model's own estimate, not a calibrated probability.

### The background workflow

`app/agents/auto_analysis.py` runs both agents as a workflow independent of the UI: a user opening an
incident never starts an agent - the UI only ever displays `incident.analysis.state`, polling it while a
run is in progress.

- **Triggers:** `start_workflow(store)` is called after every `POST /api/logs/ingest`, and once at app
  startup (`main.py`, so an incident left mid-analysis by a previous run resumes instead of staying stuck).
- **Ordering and concurrency:** incidents are queued highest priority first and run with
  `asyncio.Semaphore(MAX_CONCURRENT_ANALYSES)` (default 4), so P1s tend to finish first, though not
  strictly, since a lower-priority run already in flight keeps its slot.
- **State machine** (in memory, per incident): `queued → investigating → recommending → done`, or `failed`
  with the error message. `disabled` means `AUTO_ANALYZE=false` or Vertex AI is not configured.
- **Idempotent:** an incident with a persisted investigation and recommendations already matching its
  current occurrence count (`is_up_to_date`) is left alone, so re-ingesting unchanged logs queues nothing.
- **Shared lock:** the same per-incident `asyncio.Lock` guards the background run and the manual
  `POST /investigate` / `/recommendations` endpoints (used by "Investigate more"), so they never race.
- **One incident's failure never stops the others:** exceptions are caught per incident and recorded as
  `analysis.state = "failed"` with the error.

## 12a. The Chat Agent (a third agent)

A dashboard-wide chatbot, separate from the two-agent pipeline above. It is explicitly out of scope for the
core "detect → triage → investigate → recommend" flow described at the top of this README, and is included
as an additional, optional capability: ask free-form questions about incidents from a floating widget
(bottom-right, every page). It is a conscious third `LlmAgent` beyond that original "exactly two agents"
design, built the same way as the other two (`agents/chat_agent.py`, `tools/chat_tools.py`,
`schemas/chat.py`, `api/chat.py`) so it stays consistent rather than inventing a new pattern.

- **Triggers:** only an explicit chat message from a person. It never investigates, recommends, or starts
  the background workflow - it only reads what the other two agents and the deterministic rules already
  produced.
- **Tools** (`tools/chat_tools.py`), all read-only: `list_incidents` (compact rows, optionally filtered by
  priority/status), `get_incident_summary` (one incident's facts, root cause and top recommendation),
  `get_dashboard_overview` (the same counts as the Command Center, via the now-shared
  `services/dashboard_service.py`). No raw logs and no other incident's full evidence payload are exposed.
- **Guardrails:** always cites the incident id(s) it answers about (`referenced_incidents`, shown in the UI
  as clickable chips that open that incident); told explicitly that "open" in casual usage means "exists",
  not the literal `status="open"` enum value (this system has no "resolved" state, so every incident that
  exists is still outstanding); refuses action requests ("restart", "fix", "roll back", "approve") and
  points at that incident's Decision & Safety section instead of claiming to have done anything; says
  plainly when a question needs data this system does not have (metrics, deployments, approvals, takeover,
  verification) rather than guessing.
- **History is ephemeral:** the browser holds the last 10 turns and resends them with each message
  (`agents/chat_agent.py:build_prompt` flattens them into the prompt text); nothing is written to `data/`.
  Refreshing the page loses the conversation.

## 13. Runbooks (SIMULATED)

`data/runbooks/*.json` has one runbook per error type in the log: DATABASE_TIMEOUT, RESOURCE_PRESSURE,
INTERNAL_SERVER_ERROR, AUTHENTICATION_FAILURE, RATE_LIMIT_EXCEEDED and SLOW_REQUEST. They were **written
for this POC** and are marked `"simulated": true`. They are not real operational procedures. See
`data/runbooks/README.md`.

## 14. State

Local JSON only, written atomically (temp file + rename):
- `data/logs/parsed_logs.json`
- `data/logs/ingest_summary.json`
- `data/incidents/incidents.json`
- `data/incidents/investigations.json`
- `data/incidents/recommendations.json`

Status is derived: `open` → `investigated` → `recommendations_ready`. A new investigation clears that incident's
recommendations. Re-ingesting keeps agent results for incident ids that still exist.

The background workflow's in-flight progress (`queued`/`investigating`/`recommending`/`failed`) is **not**
persisted - it lives in memory for the life of the process. What is persisted is the result (the investigation
and recommendations above), which is what lets a restart resume correctly: `status_for()` derives `done` from
the persisted files whenever they are present and up to date, regardless of what is in memory.

## 15. Tests

```bash
cd backend && ../.venv/Scripts/python -m pytest        # 62 tests, ~10 s, no network
cd frontend && npx vitest run && npx tsc --noEmit      # 19 tests
```

Backend test files:
- `test_log_parser`
- `test_incident_detector`
- `test_workflow_classifier`
- `test_priority_engine`
- `test_evidence_service`
- `test_api` - including the background workflow: every incident reaches `recommendations_ready` with no
  manual call, a failure is isolated to its own incident, GET requests never change `analysis.state`, and
  the analytics "not enabled" metrics are reported honestly
- `test_chat` - the Chat Agent returns schema-valid replies that only reference real incident ids, 503
  without Vertex configured, an unknown incident id is reported instead of raising, and long conversation
  history is truncated to the most recent turns before it reaches the model

The tests run on a temporary copy of the real log, and they assert that the log is unchanged. The background
workflow is off by default in tests (`no_background_workflow`, autouse), so an ordinary ingest never makes a
real Vertex AI call; tests that need it opt in with the `background_workflow` fixture (the same scripted model).

The agents run through the real ADK `Runner`, using a scripted `BaseLlm` that calls every tool and then
answers through `set_model_response`. AI outputs are checked for schema and properties (confidence range,
FACT/AI_INFERENCE kinds, risk/source values, the tools called), never for wording.

## 16. Frontend

Command Center (dashboard), an incident list, and an Incident Resolution Workspace per incident - same design
system throughout (cards, badges, colours), built to read as an AI-native resolution system rather than "logs
in, recommendations out":

- **Command Center:** a two-tile lifecycle funnel (Detected, Human decision needed - Remediated/Verified/
  Resolved are not part of this POC, so they are not shown as if they were being tracked), the highest-priority
  incidents, a Priority breakdown, and connected sources and agent status (including live queue activity).
  `GET /api/dashboard/summary` also returns an `analytics` block (incident volume, priority and workflow-stage
  distribution, plus MTTD/MTTR/resolution rate/auto-remediation/approval/takeover/verification explicitly
  marked "not enabled in this POC") for API consumers; the dashboard page itself does not render it.
- **Incident list and table rows:** Incident, Workflow, Criticality, Priority and Status are separate columns
  (criticality is never merged into the workflow badge; there is no separate "AI stage" column - Status
  already carries the backend's `analysis.state`).
- **Incident Resolution Workspace:** a summary with real metric tiles, a 9-step lifecycle stepper (Detect →
  Correlate → Triage → Investigate → RCA → Decide → Remediate → Verify → Close, the last three always
  disabled), Investigation & root cause (facts vs AI inference, evidence sources), Recommendations (RUNBOOK /
  AI-GENERATED, with a banner that nothing has been executed), Decision & safety (risk, blast radius, RCA
  confidence, a `HUMAN APPROVAL REQUIRED` policy notice, and Approve/Take over shown disabled - not enabled in
  this POC - next to the one real action, **Investigate more**), a real-timestamp timeline, and a
  Remediation/Verification/Closure section that always reports "not started".
- **Every AI-stage and status badge reflects `incident.analysis.state` from the backend.** The page polls while
  the backend reports work in progress (§12, "The background workflow") and otherwise never implies the AI is
  "waiting for the page to be opened" - see `lib/lifecycle.ts`.

Views for the removed approval, remediation and agent-activity flow (from an earlier iteration of this UI) were
deleted; there are no manual "run the agent" buttons anywhere - except the chat widget (§12a), which is an
explicit, separate action by design, not a trigger for the investigate/recommend pipeline.

- **Chat widget** (`components/chat/ChatWidget.tsx`): a floating bubble, bottom-right on every page. Opens a
  panel with a plain-text conversation; an assistant reply that names incidents shows them as clickable
  `INC-XXXXXXXX` chips that open that incident's workspace. Ephemeral - the conversation lives only in this
  component's state and is lost on refresh; nothing is sent except the message and the last 10 turns.

## 17. Security notes

- No credentials are in the code or in `.env.example`. Vertex AI uses ADC.
- The browser never calls Vertex AI, and a frontend test checks that no credentials appear in the frontend
  source.
- Uploads are limited to `.log`/`.txt` files of at most 20 MB, and they are stored under generated names.
- CORS is limited to the configured origins.
