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
from pydantic import BaseModel, Field
from fastapi_mcp import FastApiMCP
from uuid import uuid4
from typing import Literal

from .mcp.grafana import GrafanaMCP
from .mcp.oncall import OnCallMCP
from .mcp.k8s import K8sMCP
from .orchestrator import orchestrate_response

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
STATIC_DIR = BASE_DIR / "static"

APP_MODE = os.getenv("APP_MODE", "all").lower()  # all | web | grafana | oncall | k8s
MCP_HTTP_TIMEOUT = float(os.getenv("MCP_HTTP_TIMEOUT", "8"))


class MCPSettings(BaseModel):
    grafana_url: Optional[str] = Field(
        default=os.getenv("GRAFANA_MCP_URL"),
        description="Base URL for Grafana MCP (e.g. http://localhost:8011/api/mcp/grafana)",
    )
    oncall_url: Optional[str] = Field(
        default=os.getenv("ONCALL_MCP_URL"),
        description="Base URL for Oncall MCP (e.g. http://localhost:8012/api/mcp/oncall)",
    )
    k8s_url: Optional[str] = Field(
        default=os.getenv("K8S_MCP_URL"),
        description="Base URL for K8s MCP (e.g. http://localhost:8013/api/mcp/k8s)",
    )
    timeout_seconds: float = Field(default=MCP_HTTP_TIMEOUT, ge=1, description="HTTP timeout to MCP endpoints.")
    enable_grafana: bool = Field(default=True, description="Enable Grafana MCP tool")
    enable_oncall: bool = Field(default=True, description="Enable Oncall MCP tool")
    enable_k8s: bool = Field(default=True, description="Enable K8s MCP tool")


class MCPEntry(BaseModel):
    id: str
    name: str
    type: Literal["grafana", "oncall", "k8s"]
    url: str
    enabled: bool = True


class MCPRegistry(BaseModel):
    entries: list[MCPEntry] = []
    active: Dict[str, Optional[str]] = {"grafana": None, "oncall": None, "k8s": None}
    reachable: Dict[str, bool] = {}

    def active_url(self, type_: str) -> Optional[str]:
        active_id = self.active.get(type_)
        if not active_id:
            return None
        for entry in self.entries:
            if entry.id == active_id and entry.enabled and entry.type == type_:
                return entry.url
        return None

    def upsert(self, name: str, type_: str, url: str, enabled: bool = True) -> MCPEntry:
        entry = MCPEntry(id=str(uuid4()), name=name, type=type_, url=url, enabled=enabled)
        self.entries.append(entry)
        # If no active set for this type, set this one.
        if not self.active.get(type_):
            self.active[type_] = entry.id
        return entry

    def set_active(self, type_: str, entry_id: str) -> bool:
        exists = any(e.id == entry_id and e.type == type_ for e in self.entries)
        if exists:
            self.active[type_] = entry_id
        return exists

    def remove(self, entry_id: str) -> bool:
        before = len(self.entries)
        self.entries = [e for e in self.entries if e.id != entry_id]
        # Clear active if it pointed to removed.
        for t, active_id in list(self.active.items()):
            if active_id == entry_id:
                self.active[t] = None
        return len(self.entries) < before

    def mark_reachable(self, entry_id: str, ok: bool) -> None:
        self.reachable[entry_id] = ok

# Instantiate lightweight MCP facades backed by JSON fixtures (used when running locally).
grafana_mcp = GrafanaMCP(DATA_DIR / "metrics.json")
oncall_mcp = OnCallMCP(DATA_DIR / "incidents.json")
k8s_mcp = K8sMCP(DATA_DIR / "k8s.json")
mcp_settings = MCPSettings()
mcp_registry = MCPRegistry()
session_history: Dict[str, list[Dict[str, Any]]] = {}

# Seed registry from env if provided.
if os.getenv("GRAFANA_MCP_URL"):
    mcp_registry.upsert(name="Grafana MCP (env)", type_="grafana", url=os.getenv("GRAFANA_MCP_URL"))
if os.getenv("ONCALL_MCP_URL"):
    mcp_registry.upsert(name="Oncall MCP (env)", type_="oncall", url=os.getenv("ONCALL_MCP_URL"))
if os.getenv("K8S_MCP_URL"):
    mcp_registry.upsert(name="K8s MCP (env)", type_="k8s", url=os.getenv("K8S_MCP_URL"))

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
    session_id: Optional[str] = None


class AckRequest(BaseModel):
    incident_id: str


class MCPUpdateRequest(MCPSettings):
    pass


class MCPRegistryAddRequest(BaseModel):
    name: str
    type: Literal["grafana", "oncall", "k8s"]
    url: str
    enabled: bool = True


class MCPRegistrySelectRequest(BaseModel):
    type: Literal["grafana", "oncall", "k8s"]
    entry_id: str


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

    session_id = req.session_id or str(uuid4())
    history = session_history.get(session_id, [])

    messages: list[Dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        *history,
        {"role": "user", "content": req.message},
    ]

    state: Dict[str, Any] = {"grafana": None, "incidents": None, "service": None, "called_tools": []}

    for _ in range(3):
        response = llm_client.chat.completions.create(
            model=LLM_MODEL,
            messages=messages,
            tools=_enabled_tools(),
            tool_choice="auto",
        )
        choice = response.choices[0].message
        tool_calls = choice.tool_calls or []

        if not tool_calls:
            reply = choice.content or "No response from model."
            reply_obj = {
                "reply": reply,
                "service": state.get("service"),
                "grafana": state.get("grafana"),
                "incidents": state.get("incidents"),
                "called_tools": state.get("called_tools"),
                "session_id": session_id,
            }
            # Store trimmed history (user + assistant turns only).
            _persist_history(session_id, history, req.message, reply)
            return reply_obj

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
    reply_obj = {
        "reply": "LLM did not return a final answer after tool calls.",
        "service": state.get("service"),
        "grafana": state.get("grafana"),
        "incidents": state.get("incidents"),
        "called_tools": state.get("called_tools"),
        "session_id": session_id,
    }
    _persist_history(session_id, history, req.message, reply_obj["reply"])
    return reply_obj


# Legacy chat endpoints retained for the frontend.
@legacy_router.post("/api/chat")
def chat_legacy(req: ChatRequest):
    return chat(req)


@legacy_router.post("/api/chat-llm")
def chat_llm_legacy(req: ChatRequest):
    return chat_llm(req)


@web_router.get("/mcp")
def get_mcp_settings() -> Dict[str, Any]:
    return mcp_settings.model_dump()


@web_router.post("/mcp")
def update_mcp_settings(req: MCPUpdateRequest) -> Dict[str, Any]:
    global mcp_settings
    mcp_settings = MCPSettings(**req.model_dump())
    return mcp_settings.model_dump()


@web_router.get("/listmcp")
def list_mcp_settings() -> Dict[str, Any]:
    return {
        "settings": mcp_settings.model_dump(),
        "registry": mcp_registry.model_dump(),
    }


@web_router.post("/mcp/registry")
def add_mcp_registry(req: MCPRegistryAddRequest) -> Dict[str, Any]:
    entry = mcp_registry.upsert(req.name, req.type, req.url, req.enabled)
    return {"entry": entry, "registry": mcp_registry.model_dump()}


@web_router.post("/mcp/registry/select")
def select_mcp_registry(req: MCPRegistrySelectRequest) -> Dict[str, Any]:
    ok = mcp_registry.set_active(req.type, req.entry_id)
    if not ok:
        raise HTTPException(status_code=404, detail="entry not found for type")
    return {"active": mcp_registry.active}


@web_router.delete("/mcp/registry/{entry_id}")
def delete_mcp_registry(entry_id: str) -> Dict[str, Any]:
    removed = mcp_registry.remove(entry_id)
    if not removed:
        raise HTTPException(status_code=404, detail="entry not found")
    return {"registry": mcp_registry.model_dump()}


def _enabled_tools() -> list[Dict[str, Any]]:
    tools = []
    if mcp_settings.enable_grafana:
        tools.append(TOOLS[0])
    if mcp_settings.enable_oncall:
        tools.extend([TOOLS[1], TOOLS[2]])
    if mcp_settings.enable_k8s:
        tools.extend([TOOLS[3], TOOLS[4], TOOLS[5]])
    return tools


def _execute_tool(name: str, args: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any] | list[Dict[str, Any]]:
    state["called_tools"] = state.get("called_tools", [])
    state["called_tools"].append(name)

    def _remote_call(method: str, url: str, *, params: Dict[str, Any] | None = None, json_body: Dict[str, Any] | None = None):
        print(f"[mcp-call] remote {method.upper()} {url} params={params} body={json_body}")
        try:
            resp = httpx.request(method, url, params=params, json=json_body, timeout=mcp_settings.timeout_seconds)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            return {"error": f"remote call failed: {exc}"}

    if name == "get_grafana_health":
        service = args.get("service")
        if not service:
            return {"error": "service required"}
        grafana_url = mcp_registry.active_url("grafana")
        if mcp_settings.grafana_url:  # backward compat direct field
            grafana_url = mcp_settings.grafana_url
        if grafana_url:
            url = f"{grafana_url.rstrip('/')}/{service}"
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
        oncall_url = mcp_registry.active_url("oncall")
        if mcp_settings.oncall_url:
            oncall_url = mcp_settings.oncall_url
        if oncall_url:
            url = f"{oncall_url.rstrip('/')}/incidents"
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
        oncall_url = mcp_registry.active_url("oncall")
        if mcp_settings.oncall_url:
            oncall_url = mcp_settings.oncall_url
        if oncall_url:
            url = f"{oncall_url.rstrip('/')}/incidents/ack"
            updated = _remote_call("POST", url, json_body={"incident_id": incident_id})
            return updated
        updated = oncall_mcp.acknowledge(incident_id)
        return updated or {"error": "incident not found"}

    if name == "k8s_overview":
        k8s_url = mcp_registry.active_url("k8s")
        if mcp_settings.k8s_url:
            k8s_url = mcp_settings.k8s_url
        if k8s_url:
            url = f"{k8s_url.rstrip('/')}/overview"
            overview = _remote_call("GET", url)
        else:
            overview = k8s_mcp.cluster_overview()
        state["k8s_overview"] = overview
        return overview

    if name == "list_pods":
        namespace = args.get("namespace")
        k8s_url = mcp_registry.active_url("k8s")
        if mcp_settings.k8s_url:
            k8s_url = mcp_settings.k8s_url
        if k8s_url:
            url = f"{k8s_url.rstrip('/')}/pods"
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
        k8s_url = mcp_registry.active_url("k8s")
        if mcp_settings.k8s_url:
            k8s_url = mcp_settings.k8s_url
        if k8s_url:
            url = f"{k8s_url.rstrip('/')}/pods/restart"
            pod = _remote_call("POST", url, json_body={"namespace": namespace, "name": name_arg})
        else:
            pod = k8s_mcp.restart_pod(namespace, name_arg)
        return pod or {"error": "pod not found"}

    return {"error": f"unknown tool {name}"}


def _persist_history(session_id: str, history: list[Dict[str, Any]], user_msg: str, assistant_msg: str) -> None:
    # Keep only user/assistant turns to bound size.
    new_history = [m for m in history if m.get("role") in {"user", "assistant"}]
    new_history.append({"role": "user", "content": user_msg})
    new_history.append({"role": "assistant", "content": assistant_msg})
    session_history[session_id] = new_history[-12:]


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
