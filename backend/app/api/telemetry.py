from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool

from app.agents.auto_analysis import start_workflow
from app.config.settings import settings
from app.services.ingestion import ingest
from app.state.store import get_store
from app.telemetry import otel
from app.telemetry.metric_detection import detect_metric_events

router = APIRouter(prefix="/api/telemetry", tags=["telemetry"])
log = logging.getLogger(__name__)


@router.get("/status")
def telemetry_status() -> dict:
    return otel.status()


_pull_lock = asyncio.Lock()  # the button, the API and the background poll never read at the same time


async def pull_once(store) -> dict:
    """Read logs + metric anomalies from the application's telemetry stack and run detection. Raises
    TelemetryUnavailable when the log backend cannot be read."""
    async with _pull_lock:
        application = store.application.application_name if store.application else None
        entries = await run_in_threadpool(otel.fetch_logs, application)
        metric_events, metrics_error = [], None
        try:  # Prometheus being down must not hide what the logs show; it is reported instead
            metric_events = await run_in_threadpool(detect_metric_events, application)
        except otel.TelemetryUnavailable as exc:
            metrics_error = str(exc)
        entries = entries + metric_events
        stored = await run_in_threadpool(otel.save_events, store.data_dir, entries)
        summary = await run_in_threadpool(ingest, store)
        start_workflow(store)
        return {"pulled": len(entries) - len(metric_events), "metric_anomaly_events": len(metric_events),
                "metrics_error": metrics_error, "stored_events": len(stored),
                "incidents_detected": summary.incidents_detected, "services": summary.services}


@router.post("/pull")
async def pull_telemetry() -> dict:
    """Read recent logs and metric anomalies from the configured OpenTelemetry stack, convert them to the incident
    event model and run the normal detection / triage workflow over everything known."""
    try:
        return await pull_once(get_store())
    except otel.TelemetryUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc


async def auto_pull_once() -> dict | None:
    """One background poll. Does nothing until an application has been scanned; failures are logged, never raised."""
    store = get_store()
    if store.application is None:
        return None
    try:
        return await pull_once(store)
    except otel.TelemetryUnavailable as exc:
        log.warning("Automatic telemetry read skipped: %s", exc)
    except Exception:  # noqa: BLE001 - the poll loop must survive any single failure
        log.exception("Automatic telemetry read failed")
    return None


async def poll_loop() -> None:
    await asyncio.sleep(5)
    while True:
        await auto_pull_once()
        await asyncio.sleep(settings.telemetry_poll_seconds)
