from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Any


class GrafanaMCP:
    """Lightweight Grafana MCP facade backed by static JSON data.

    In a real deployment this would call Grafana's HTTP API. Here we keep it
    dependency-free so the PoC can run without external services.
    """

    def __init__(self, metrics_path: Path | str):
        self.metrics_path = Path(metrics_path)
        self._metrics = self._load_metrics()

    def _load_metrics(self) -> Dict[str, Dict[str, Any]]:
        if not self.metrics_path.exists():
            return {}
        with self.metrics_path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def refresh(self) -> None:
        """Reloads metrics from disk."""
        self._metrics = self._load_metrics()

    def list_services(self) -> list[str]:
        return sorted(self._metrics.keys())

    def query_health(self, service: str) -> Dict[str, Any] | None:
        data = self._metrics.get(service)
        if not data:
            return None
        return {
            "service": service,
            "p99_latency_ms": data.get("p99_latency_ms"),
            "error_rate": data.get("error_rate"),
            "cpu_utilization": data.get("cpu_utilization"),
            "traffic_rps": data.get("traffic_rps"),
            "last_updated": data.get("last_updated"),
            "panel_url": self._panel_url(service),
        }

    def _panel_url(self, service: str) -> str:
        # Placeholder to show where a real Grafana deep-link would go.
        return f"https://grafana.example.com/d/onduty/{service}?view=overview"


__all__ = ["GrafanaMCP"]
