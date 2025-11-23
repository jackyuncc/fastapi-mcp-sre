from __future__ import annotations

from typing import Any, Dict, List, Optional

from .mcp.grafana import GrafanaMCP
from .mcp.oncall import OnCallMCP


def detect_service(message: str, available: List[str]) -> Optional[str]:
    lower = message.lower()
    for service in available:
        if service in lower:
            return service
    # allow users to type "orders" instead of "orders-service"
    for service in available:
        short = service.replace("-service", "")
        if short in lower:
            return service
    return available[0] if available else None


def orchestrate_response(message: str, grafana: GrafanaMCP, oncall: OnCallMCP) -> Dict[str, Any]:
    grafana.refresh()
    oncall.refresh()

    services = grafana.list_services()
    service = detect_service(message, services)

    grafana_snapshot = grafana.query_health(service) if service else None
    incidents = oncall.active_incidents(service)

    reply_parts: List[str] = []
    if service:
        reply_parts.append(f"Looking at {service}…")

    if grafana_snapshot:
        reply_parts.append(
            _format_metrics(
                grafana_snapshot.get("p99_latency_ms"),
                grafana_snapshot.get("error_rate"),
                grafana_snapshot.get("cpu_utilization"),
                grafana_snapshot.get("traffic_rps"),
            )
        )
        reply_parts.append(f"Grafana panel: {grafana_snapshot['panel_url']}")
    else:
        reply_parts.append("No Grafana data available yet.")

    if incidents:
        incident_lines = [
            f"{inc['id']} ({inc['severity']}): {inc['summary']} [status={inc['status']}]"
            for inc in incidents
        ]
        reply_parts.append("Active incidents:\n" + "\n".join(incident_lines))
    else:
        reply_parts.append("No active incidents.")

    return {
        "reply": "\n\n".join(reply_parts),
        "service": service,
        "grafana": grafana_snapshot,
        "incidents": incidents,
        "called_tools": [tool for tool in ["grafana-mcp", "oncall-mcp"] if tool],
    }


def _format_metrics(latency: Any, error_rate: Any, cpu: Any, rps: Any) -> str:
    parts = []
    if latency is not None:
        parts.append(f"p99 latency {latency} ms")
    if error_rate is not None:
        parts.append(f"error rate {error_rate * 100:.2f}%")
    if cpu is not None:
        parts.append(f"CPU {cpu * 100:.1f}%")
    if rps is not None:
        parts.append(f"traffic {rps} rps")
    return " | ".join(parts) if parts else "No metrics"


__all__ = ["orchestrate_response"]
