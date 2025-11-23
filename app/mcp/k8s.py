from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List


class K8sMCP:
    """Lightweight Kubernetes MCP facade backed by a JSON fixture."""

    def __init__(self, k8s_path: Path | str):
        self.k8s_path = Path(k8s_path)
        self._state = self._load_state()

    def _load_state(self) -> Dict[str, Any]:
        if not self.k8s_path.exists():
            return {"nodes": [], "pods": []}
        with self.k8s_path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def refresh(self) -> None:
        self._state = self._load_state()

    def cluster_overview(self) -> Dict[str, Any]:
        nodes = self._state.get("nodes", [])
        pods = self._state.get("pods", [])
        return {
            "nodes": nodes,
            "pod_count": len(pods),
            "namespaces": sorted({p.get("namespace", "default") for p in pods}),
        }

    def list_pods(self, namespace: str | None = None) -> List[Dict[str, Any]]:
        pods = self._state.get("pods", [])
        if namespace:
            pods = [p for p in pods if p.get("namespace") == namespace]
        return pods

    def restart_pod(self, namespace: str, name: str) -> Dict[str, Any] | None:
        for pod in self._state.get("pods", []):
            if pod.get("namespace") == namespace and pod.get("name") == name:
                pod["restart_count"] = pod.get("restart_count", 0) + 1
                pod["last_restart"] = "now"
                pod["status"] = "Running"
                self._persist()
                return pod
        return None

    def _persist(self) -> None:
        with self.k8s_path.open("w", encoding="utf-8") as f:
            json.dump(self._state, f, indent=2)


__all__ = ["K8sMCP"]
