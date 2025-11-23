from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List


class OnCallMCP:
    """Simple on-call/incident MCP backed by a JSON fixture."""

    def __init__(self, incidents_path: Path | str):
        self.incidents_path = Path(incidents_path)
        self._incidents = self._load_incidents()

    def _load_incidents(self) -> List[Dict[str, Any]]:
        if not self.incidents_path.exists():
            return []
        with self.incidents_path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def refresh(self) -> None:
        self._incidents = self._load_incidents()

    def active_incidents(self, service: str | None = None) -> List[Dict[str, Any]]:
        incidents = [i for i in self._incidents if i.get("status") in {"triggered", "acknowledged"}]
        if service:
            incidents = [i for i in incidents if i.get("service") == service]
        return incidents

    def acknowledge(self, incident_id: str) -> Dict[str, Any] | None:
        for incident in self._incidents:
            if incident.get("id") == incident_id:
                incident["status"] = "acknowledged"
                self._persist()
                return incident
        return None

    def _persist(self) -> None:
        # Persist updated statuses so consecutive requests see the change.
        with self.incidents_path.open("w", encoding="utf-8") as f:
            json.dump(self._incidents, f, indent=2)


__all__ = ["OnCallMCP"]
