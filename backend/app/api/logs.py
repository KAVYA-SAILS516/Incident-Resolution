from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

from app.agents.auto_analysis import start_workflow
from app.config.settings import settings
from app.schemas.log import IngestSummary
from app.services.ingestion import LOG_SUFFIXES, ingest, save_upload
from app.state.store import get_store

router = APIRouter(prefix="/api/logs", tags=["logs"])

MAX_UPLOAD_BYTES = 20 * 1024 * 1024


@router.post("/ingest", response_model=IngestSummary)
async def ingest_logs(file: UploadFile | None = File(default=None)) -> IngestSummary:
    """Legacy plain-log-file import. Disabled at runtime (FILE_LOG_INGEST=false): the monitored application's
    telemetry is read with POST /api/telemetry/pull, and nothing falls back to local log files."""
    if not settings.file_log_ingest:
        raise HTTPException(410, "File log import is disabled. Telemetry is read from the monitored application's "
                                 "OpenTelemetry stack: POST /api/telemetry/pull.")
    if file is not None and file.filename:
        suffix = "." + file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
        if suffix not in LOG_SUFFIXES:
            raise HTTPException(400, f"Unsupported file type; expected one of {sorted(LOG_SUFFIXES)}")
        content = await file.read()
        if not content.strip():
            raise HTTPException(400, "Uploaded file is empty")
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "Uploaded file is larger than 20 MB")
    store = get_store()
    logs_dir = store.data_dir / "logs"
    if file is not None and file.filename:
        await run_in_threadpool(save_upload, logs_dir, file.filename, content)
    summary = await run_in_threadpool(ingest, store, logs_dir)
    # Start the background resolution workflow for every incident, highest priority first. This is a
    # backend event (ingest finished), not a UI action - the frontend only ever reads the resulting state.
    start_workflow(store)
    return summary


@router.get("/summary", response_model=IngestSummary)
def ingest_summary() -> IngestSummary:
    summary = get_store().summary
    if summary is None:
        raise HTTPException(404, "No logs ingested yet; POST /api/logs/ingest")
    return summary
