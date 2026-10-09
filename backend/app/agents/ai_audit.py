"""Audit trail of AI calls: which agent used which provider/model, how long it took and whether the output
validated. Only metadata is stored (never prompts, credentials or model output), as JSON lines in
data/incidents/ai_calls.jsonl."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

MAX_RETURNED = 200


def _path() -> Path:
    from app.state.store import get_store  # late import: the store is configured per process / per test

    return get_store().data_dir / "incidents" / "ai_calls.jsonl"


def record(*, agent: str, provider: str, model: str, duration_ms: int, success: bool, validation: str,
           context_chars: int, error: str | None = None, tools_called: list[str] | None = None) -> None:
    entry = {"at": datetime.now(timezone.utc).isoformat(), "agent": agent, "provider": provider, "model": model,
             "duration_ms": duration_ms, "success": success, "validation": validation, "context_chars": context_chars,
             "tools_called": tools_called or [], "error": (error or "")[:300] or None}
    try:
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
    except OSError:
        pass  # auditing must never break an investigation


def recent() -> list[dict]:
    try:
        lines = _path().read_text(encoding="utf-8").splitlines()[-MAX_RETURNED:]
    except OSError:
        return []
    return [json.loads(line) for line in lines if line.strip()]
