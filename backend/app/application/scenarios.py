"""Failure scenarios as evidence: which documented / configured failure scenarios are relevant to an incident, and
whether the application's configuration currently switches them on.

Nothing here decides a root cause. It re-reads the scenario's own configuration file (read-only, inside the scanned
application root) so the state is current rather than as of the last scan, and reports where each fact came from.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app.application import parser
from app.application.models import ApplicationKnowledge, FailureScenario
from app.application.reader import MAX_FILE_BYTES

CONFIG_SOURCE = "application configuration"


def _current_state(root: Path, scenario: FailureScenario) -> tuple[bool | None, str | None, str]:
    """(enabled, active variant, how it was determined) from the scenario's configuration file right now."""
    try:
        path = (root / scenario.source).resolve()
        path.relative_to(root.resolve())  # never leave the application root
        if path.stat().st_size > MAX_FILE_BYTES:
            raise OSError("too large")
        flags = {f["name"]: f for f in parser.parse_flags(path.read_text(encoding="utf-8", errors="replace"), scenario.source)}
        flag = flags.get(scenario.name)
        if flag is not None:
            return flag["enabled"], flag["active_variant"], f"read from {scenario.source} just now"
    except (OSError, ValueError):
        pass
    return scenario.enabled, scenario.active_variant, f"{scenario.source} could not be re-read; value is from the last scan"


def relevant_failure_scenarios(knowledge: ApplicationKnowledge | None, services: dict[str, str]) -> list[dict]:
    """Scenarios whose owning service is one of `services` ({service: why it is relevant})."""
    if knowledge is None:
        return []
    root = Path(knowledge.application_path)
    found = []
    for scenario in knowledge.failure_scenarios:
        if scenario.service not in services:
            continue
        enabled, variant, how = _current_state(root, scenario)
        found.append({
            "scenario": scenario.name, "service": scenario.service, "relevance": services[scenario.service],
            "enabled": enabled if enabled is not None else "UNKNOWN", "active_variant": variant,
            "available_variants": scenario.variants, "description": scenario.description,
            "source": f"{CONFIG_SOURCE}: {scenario.source} ({how})",
            "documented_in": scenario.documented_in or None,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        })
    return sorted(found, key=lambda s: (s["enabled"] is not True, s["scenario"]))
