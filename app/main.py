from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from openai import OpenAI
from pydantic import BaseModel
from fastapi_mcp import FastApiMCP

from .mcp.grafana import GrafanaMCP
from .mcp.oncall import OnCallMCP
from .mcp.k8s import K8sMCP
from .orchestrator import orchestrate_response

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
STATIC_DIR = BASE_DIR / "static"

APP_MODE = os.getenv("APP_MODE", "all").lower()  # all | web | grafana | oncall | k8s
GRAFANA_MCP_URL = os.getenv("GRAFANA_MCP_URL")  # if set, web calls Grafana MCP remotely
ONCALL_MCP_URL = os.getenv("ONCALL_MCP_URL")  # if set, web calls Oncall MCP remotely
K8S_MCP_URL = os.getenv("K8S_MCP_URL")  # if set, web calls K8s MCP remotely
MCP_HTTP_TIMEOUT = float(os.getenv("MCP_HTTP_TIMEOUT", "8"))

# Instantiate lightweight MCP facades backed by JSON fixtures (used when running locally).
grafana_mcp = GrafanaMCP(DATA_DIR / "metrics.json")
oncall_mcp = OnCallMCP(DATA_DIR / "incidents.json")
k8s_mcp = K8sMCP(DATA_DIR / "k8s.json")

web_router = APIRouter(prefix="/api/web", tags=["web-chat"])
grafana_router = APIRouter(prefix="/api/mcp/grafana", tags=["grafana-mcp"])
k8s_router = APIRouter(prefix="/api/mcp/k8s", tags=["k8s-mcp"])
oncall_router = APIRouter(prefix="/api/mcp/oncall", tags=["oncall-mcp"])
legacy_router = APIRouter(tags=["legacy"])

# LLM configuration (OpenAI-compatible). Works with OpenAI or local endpoints that follow the same API.
LLM_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
LLM_API_KEY = os.getenv("OPENAI_API_KEY")
LLM_BASE_URL = os.getenv("OPENAI_BASE_URL")
llm_client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL or None) if LLM_API_KEY else None

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_grafana_health",
            "description": "Fetch Grafana metrics for a specific service.",
            "parameters": {
                "type": "object",
                "properties": {
                    "service": {"type": "string", "description": "Service name (e.g., orders-service)."}
                },
                "required": ["service"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_incidents",
            "description": "List active incidents, optionally filtered by service.",
            "parameters": {
                "type": "object",
                "properties": {
                    "service": {"type": "string", "description": "Optional service to filter incidents."}
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ack_incident",
            "description": "Acknowledge an incident by ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "incident_id": {"type": "string", "description": "Incident identifier, e.g., INC-1203."}
                },
                "required": ["incident_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "k8s_overview",
            "description": "Get cluster overview (nodes, namespaces, pod count).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_pods",
            "description": "List pods, optionally filtered by namespace.",
            "parameters": {
                "type": "object",
                "properties": {"namespace": {"type": "string", "description": "Namespace to filter pods."}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "restart_pod",
            "description": "Restart a pod by namespace and name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "namespace": {"type": "string"},
                    "name": {"type": "string"},
                },
                "required": ["namespace", "name"],
            },
        },
    },
]


class ChatRequest(BaseModel):
    message: str


class AckRequest(BaseModel):
    incident_id: str


@grafana_router.get("/services")
def list_services() -> list[str]:
    return grafana_mcp.list_services()


@grafana_router.get("/{service}")
def grafana_health(service: str):
    data = grafana_mcp.query_health(service)
    if not data:
        raise HTTPException(status_code=404, detail="service not found")
    return data


@oncall_router.get("/incidents")
def get_incidents(service: Optional[str] = None):
    return oncall_mcp.active_incidents(service)


@oncall_router.post("/incidents/ack")
def acknowledge_incident(req: AckRequest):
    updated = oncall_mcp.acknowledge(req.incident_id)
    if not updated:
        raise HTTPException(status_code=404, detail="incident not found")
    return updated


# Legacy/compat endpoints used by the frontend (retain stable paths).
@legacy_router.get("/api/services")
def list_services_legacy() -> list[str]:
    return grafana_mcp.list_services()


@legacy_router.get("/api/grafana/{service}")
def grafana_health_legacy(service: str):
    data = grafana_mcp.query_health(service)
    if not data:
        raise HTTPException(status_code=404, detail="service not found")
    return data


@legacy_router.get("/api/incidents")
def get_incidents_legacy(service: Optional[str] = None):
    return oncall_mcp.active_incidents(service)


@legacy_router.post("/api/incidents/ack")
def acknowledge_incident_legacy(req: AckRequest):
    updated = oncall_mcp.acknowledge(req.incident_id)
    if not updated:
        raise HTTPException(status_code=404, detail="incident not found")
    return updated


# --- K8s MCP endpoints ---
@k8s_router.get("/overview")
def k8s_overview():
    k8s_mcp.refresh()
    return k8s_mcp.cluster_overview()


@k8s_router.get("/pods")
def k8s_pods(namespace: Optional[str] = None):
    k8s_mcp.refresh()
    return k8s_mcp.list_pods(namespace)


class RestartPodRequest(BaseModel):
    namespace: str
    name: str


@k8s_router.post("/pods/restart")
def restart_pod(req: RestartPodRequest):
    pod = k8s_mcp.restart_pod(req.namespace, req.name)
    if not pod:
        raise HTTPException(status_code=404, detail="pod not found")
    return pod


@web_router.post("/chat")
def chat(req: ChatRequest):
    return orchestrate_response(req.message, grafana_mcp, oncall_mcp)


@web_router.post("/chat-llm")
def chat_llm(req: ChatRequest):
    if not llm_client:
        raise HTTPException(
            status_code=503,
            detail="LLM not configured. Set OPENAI_API_KEY (and optionally OPENAI_MODEL/OPENAI_BASE_URL).",
        )

    grafana_mcp.refresh()
    oncall_mcp.refresh()
    k8s_mcp.refresh()

    services = grafana_mcp.list_services()
    system_prompt = (
        "You are an on-call assistant. Use the provided tools to answer with concise operational context. "
        f"Available services: {', '.join(services) if services else 'none'}. "
        "Prefer calling tools to ground your answers. Keep responses brief."
    )

    messages: list[Dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": req.message},
    ]

    state: Dict[str, Any] = {"grafana": None, "incidents": None, "service": None, "called_tools": []}

    for _ in range(3):
        response = llm_client.chat.completions.create(
            model=LLM_MODEL,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
        )
        choice = response.choices[0].message
        tool_calls = choice.tool_calls or []

        if not tool_calls:
            reply = choice.content or "No response from model."
            return {
                "reply": reply,
                "service": state.get("service"),
                "grafana": state.get("grafana"),
                "incidents": state.get("incidents"),
                "called_tools": state.get("called_tools"),
            }

        messages.append(
            {
                "role": "assistant",
                "content": choice.content,
                "tool_calls": [tc.model_dump() for tc in tool_calls],
            }
        )

        for call in tool_calls:
            tool_name = call.function.name
            try:
                args = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}

            result = _execute_tool(tool_name, args, state)
            messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(result)})

    # Fallback if the model never returned a final message.
    return {
        "reply": "LLM did not return a final answer after tool calls.",
        "service": state.get("service"),
        "grafana": state.get("grafana"),
        "incidents": state.get("incidents"),
        "called_tools": state.get("called_tools"),
    }


# Legacy chat endpoints retained for the frontend.
@legacy_router.post("/api/chat")
def chat_legacy(req: ChatRequest):
    return chat(req)


@legacy_router.post("/api/chat-llm")
def chat_llm_legacy(req: ChatRequest):
    return chat_llm(req)


def _execute_tool(name: str, args: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any] | list[Dict[str, Any]]:
    state["called_tools"] = state.get("called_tools", [])
    state["called_tools"].append(name)

    def _remote_call(method: str, url: str, *, params: Dict[str, Any] | None = None, json_body: Dict[str, Any] | None = None):
        try:
            resp = httpx.request(method, url, params=params, json=json_body, timeout=MCP_HTTP_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            return {"error": f"remote call failed: {exc}"}

    if name == "get_grafana_health":
        service = args.get("service")
        if not service:
            return {"error": "service required"}
        if GRAFANA_MCP_URL:
            url = f"{GRAFANA_MCP_URL.rstrip('/')}/{service}"
            data = _remote_call("GET", url)
        else:
            data = grafana_mcp.query_health(service)
        if not data:
            return {"error": "service not found"}
        state["grafana"] = data
        state["service"] = service
        return data

    if name == "list_incidents":
        service = args.get("service")
        if ONCALL_MCP_URL:
            url = f"{ONCALL_MCP_URL.rstrip('/')}/incidents"
            incidents = _remote_call("GET", url, params={"service": service} if service else None)
        else:
            incidents = oncall_mcp.active_incidents(service)
        if service:
            state["service"] = service
        state["incidents"] = incidents
        return incidents

    if name == "ack_incident":
        incident_id = args.get("incident_id")
        if not incident_id:
            return {"error": "incident_id required"}
        if ONCALL_MCP_URL:
            url = f"{ONCALL_MCP_URL.rstrip('/')}/incidents/ack"
            updated = _remote_call("POST", url, json_body={"incident_id": incident_id})
            return updated
        updated = oncall_mcp.acknowledge(incident_id)
        return updated or {"error": "incident not found"}

    if name == "k8s_overview":
        if K8S_MCP_URL:
            url = f"{K8S_MCP_URL.rstrip('/')}/overview"
            overview = _remote_call("GET", url)
        else:
            overview = k8s_mcp.cluster_overview()
        state["k8s_overview"] = overview
        return overview

    if name == "list_pods":
        namespace = args.get("namespace")
        if K8S_MCP_URL:
            url = f"{K8S_MCP_URL.rstrip('/')}/pods"
            pods = _remote_call("GET", url, params={"namespace": namespace} if namespace else None)
        else:
            pods = k8s_mcp.list_pods(namespace)
        state["k8s_pods"] = pods
        return pods

    if name == "restart_pod":
        namespace = args.get("namespace")
        name_arg = args.get("name")
        if not namespace or not name_arg:
            return {"error": "namespace and name required"}
        if K8S_MCP_URL:
            url = f"{K8S_MCP_URL.rstrip('/')}/pods/restart"
            pod = _remote_call("POST", url, json_body={"namespace": namespace, "name": name_arg})
        else:
            pod = k8s_mcp.restart_pod(namespace, name_arg)
        return pod or {"error": "pod not found"}

    return {"error": f"unknown tool {name}"}


# Serve static PoC frontend and include routers based on APP_MODE.
def build_app(mode: str | None = None) -> FastAPI:
    mode = (mode or APP_MODE).lower()
    app = FastAPI(title="OnDuty Assistant PoC")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    if mode in {"all", "web"}:
        app.include_router(web_router)
        app.include_router(legacy_router)
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    if mode in {"all", "grafana"}:
        app.include_router(grafana_router)
    if mode in {"all", "oncall"}:
        app.include_router(oncall_router)
    if mode in {"all", "k8s"}:
        app.include_router(k8s_router)

    mcp_tags: list[str] = []
    if mode in {"all", "grafana"}:
        mcp_tags.append("grafana-mcp")
    if mode in {"all", "oncall"}:
        mcp_tags.append("oncall-mcp")
    if mode in {"all", "k8s"}:
        mcp_tags.append("k8s-mcp")
    if mcp_tags:
        # Expose FastAPI endpoints as MCP tools via fastapi-mcp, filtered by tags present.
        FastApiMCP(app, include_tags=mcp_tags).mount()

    return app


# Initialize app after all helpers are defined so MCP registration can use _execute_tool.
app = build_app()
