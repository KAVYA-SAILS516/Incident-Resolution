"""Guarded remediation. Only actions in the registered catalogue (config/resolution_rules.ACTIONS) on
services known to the scanned application can run, and only options this system itself proposed. No shell
command is ever built from model output, and no subprocess is started here.

POC executor: actions are SIMULATED (recorded, nothing touched). Verification still measures real telemetry,
so a simulated action that does not change the system is correctly reported as not recovered.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from app.config import resolution_rules as rules
from app.schemas.resolution import RemediationAttempt, ResolutionOption, ResolutionRecord
from app.state.store import JsonStore

REMEDIATION_MODE = "simulated"
_SERVICE_NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,62}$")


class RemediationRejected(Exception):
    """The requested remediation is not allowed (message is safe to show to the user)."""


def _check(store: JsonStore, record: ResolutionRecord, option_id: str, approved_by: str | None) -> ResolutionOption:
    option = next((o for o in record.options if o.option_id == option_id), None)
    if option is None:
        raise RemediationRejected(f"'{option_id}' is not an option proposed for this incident")
    if record.state == "resolved":
        raise RemediationRejected("The incident is already resolved")
    if any(a.status == "executed" and not any(v.attempt_id == a.attempt_id for v in record.verifications)
           for a in record.attempts):
        raise RemediationRejected("The previous action has not been verified yet; verify it before acting again")
    if option.action not in rules.ACTIONS:
        raise RemediationRejected(f"Action '{option.action}' is not a registered remediation action")
    if not _SERVICE_NAME.match(option.target_service) or store.application is None \
            or store.application.service(option.target_service) is None:
        raise RemediationRejected(f"'{option.target_service}' is not a known service of the scanned application")
    if option.execution_mode == "HUMAN_TAKEOVER":
        raise RemediationRejected("Policy: this option must be carried out by a person, not by the system")
    if option.execution_mode == "HUMAN_APPROVAL" and not (approved_by or "").strip():
        raise RemediationRejected("Policy: this option requires human approval")
    return option


def execute(store: JsonStore, incident_id: str, option_id: str, approved_by: str | None) -> RemediationAttempt:
    record = store.resolutions.get(incident_id)
    if record is None:
        raise RemediationRejected("No resolution has been proposed for this incident")
    option = _check(store, record, option_id, approved_by)
    attempt = RemediationAttempt(
        attempt_id=uuid.uuid4().hex[:8], option_id=option.option_id, action=option.action,
        target_service=option.target_service, mode=REMEDIATION_MODE, status="executed",
        approved_by=(approved_by or None) if option.execution_mode == "HUMAN_APPROVAL" else "policy (auto)",
        detail=f"Simulated: {option.description}. No infrastructure was changed.",
        executed_at=datetime.now(timezone.utc),
    )
    record.attempts.append(attempt)
    record.state = "executed"
    record.next_step = "Verify recovery from telemetry."
    record.updated_at = attempt.executed_at
    store.save_resolution(record)
    return attempt
