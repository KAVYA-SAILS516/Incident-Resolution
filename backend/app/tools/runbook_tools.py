"""Runbook access. Runbooks are SIMULATED JSON files in data/runbooks/ (see its README)."""

from __future__ import annotations

import json
from pathlib import Path

from app.config.settings import settings


def list_runbooks(directory: Path | None = None) -> list[dict]:
    directory = directory or settings.runbooks_dir
    runbooks = []
    for path in sorted(directory.glob("*.json")):
        try:
            runbooks.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue  # a broken runbook file must not break investigations
    return runbooks


def load_runbook(error_type: str, directory: Path | None = None) -> dict | None:
    wanted = (error_type or "").upper()
    return next((rb for rb in list_runbooks(directory) if str(rb.get("error_type", "")).upper() == wanted), None)


def runbook_step_titles(runbook: dict | None) -> list[str]:
    return [step["title"] for step in (runbook or {}).get("steps", []) if step.get("title")]


def make_runbook_tools(error_type: str) -> list:
    def get_runbook() -> dict:
        """Return the (simulated) runbook for this incident's error type, or {"available": false}.

        Recommendations taken from it must use a step title verbatim and source "runbook".
        """
        runbook = load_runbook(error_type)
        if runbook is None:
            return {"available": False, "error_type": error_type,
                    "note": "No runbook exists; every recommendation must be source AI-generated."}
        return {"available": True, **runbook}

    return [get_runbook]
