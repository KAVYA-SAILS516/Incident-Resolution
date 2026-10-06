"""Background incident-resolution workflow: the Investigation and Recommendation agents run independently
of the UI, as soon as an incident exists, highest priority first. The UI only ever reads this state (via
Incident.analysis and the dashboard queue counts) - opening an incident never starts an agent.

The workflow is started by backend events: after an ingest, and at app startup for any incident left
over from a previous run (e.g. the process was restarted mid-analysis). The per-incident progress
(queued/investigating/recommending) is transient and kept in memory; the finished results are persisted
by the store as usual, so "done" survives a restart.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from app.agents.investigation_agent import investigate
from app.agents.recommendation_agent import recommend
from app.config.settings import settings
from app.state.store import JsonStore

log = logging.getLogger(__name__)

RUNNING_STATES = ("queued", "investigating", "recommending")

# One agent run per incident at a time, shared with the manual "investigate more" endpoints so a
# background run and a human-triggered re-run never race each other.
_locks: dict[str, asyncio.Lock] = {}


def lock_for(incident_id: str) -> asyncio.Lock:
    return _locks.setdefault(incident_id, asyncio.Lock())


@dataclass
class AnalysisState:
    state: str  # queued | investigating | recommending | done | failed
    error: str | None = None


_states: dict[str, AnalysisState] = {}
_tasks: set[asyncio.Task] = set()  # keep references so tasks are not garbage-collected mid-run


def is_up_to_date(store: JsonStore, incident_id: str) -> bool:
    incident = store.incidents[incident_id]
    investigation = store.investigations.get(incident_id)
    return (incident_id in store.recommendations and investigation is not None
            and investigation.based_on_occurrences == incident.occurrences)


def status_for(store: JsonStore, incident_id: str) -> AnalysisState:
    """The backend-persisted truth for one incident: what the UI displays, verbatim.

    Persisted results (an up-to-date investigation + recommendations) always win, even over a stale
    in-memory "failed" from an earlier attempt - for example after a manual re-run succeeds.
    """
    if is_up_to_date(store, incident_id):
        return AnalysisState("done")
    live = _states.get(incident_id)
    if live is not None:
        return live
    if not settings.auto_analyze:
        return AnalysisState("disabled", "Automatic analysis is turned off (AUTO_ANALYZE=false)")
    if not settings.llm_configured:
        return AnalysisState("disabled", "Vertex AI is not configured")
    return AnalysisState("queued")  # about to be picked up, or waiting for a slot


def queue_counts(store: JsonStore) -> dict[str, int]:
    counts = {"queued": 0, "running": 0, "failed": 0}
    for incident_id in store.incidents:
        state = status_for(store, incident_id).state
        if state == "queued":
            counts["queued"] += 1
        elif state in ("investigating", "recommending"):
            counts["running"] += 1
        elif state == "failed":
            counts["failed"] += 1
    return counts


def reset() -> None:
    """Forget progress and cancel running work (tests only)."""
    for task in list(_tasks):
        task.cancel()
    _tasks.clear()
    _states.clear()
    _locks.clear()


async def analyze_incident(store: JsonStore, incident_id: str, *, force: bool = False) -> None:
    """Investigate, then recommend. Failures are recorded on the incident state, never raised."""
    async with lock_for(incident_id):
        incident = store.incidents.get(incident_id)
        if incident is None:
            _states.pop(incident_id, None)
            return
        if not force and is_up_to_date(store, incident_id):
            _states[incident_id] = AnalysisState("done")
            return
        try:
            _states[incident_id] = AnalysisState("investigating")
            investigation = await investigate(incident, store)
            store.save_investigation(investigation)
            _states[incident_id] = AnalysisState("recommending")
            store.save_recommendations(await recommend(incident, investigation, store))
            _states[incident_id] = AnalysisState("done")
        except asyncio.CancelledError:
            _states.pop(incident_id, None)
            raise
        except Exception as exc:  # noqa: BLE001 - one failed incident must not stop the workflow
            log.warning("Automatic analysis of %s failed: %s", incident_id, exc)
            _states[incident_id] = AnalysisState("failed", str(exc))


async def _run_queue(store: JsonStore, incident_ids: list[str]) -> None:
    semaphore = asyncio.Semaphore(settings.max_concurrent_analyses)

    async def one(incident_id: str) -> None:
        async with semaphore:
            await analyze_incident(store, incident_id)

    await asyncio.gather(*(one(i) for i in incident_ids))


def start_workflow(store: JsonStore) -> int:
    """Queue every incident whose analysis is missing or stale, highest priority first.

    Safe to call repeatedly (after every ingest, and at startup): an incident already queued, running,
    or up to date is left alone. Returns how many incidents were newly queued.
    """
    if not (settings.auto_analyze and settings.llm_configured):
        return 0
    ordered = sorted(store.incidents.values(), key=lambda i: (-i.priority_score, i.first_seen))
    pending = [i.incident_id for i in ordered
               if not is_up_to_date(store, i.incident_id)
               and (_states.get(i.incident_id) is None or _states[i.incident_id].state not in RUNNING_STATES)]
    for incident_id in pending:
        _states[incident_id] = AnalysisState("queued")
    if pending:
        task = asyncio.get_running_loop().create_task(_run_queue(store, pending))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    return len(pending)
