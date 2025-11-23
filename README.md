## OnDuty AI Assistant (PoC)

Lightweight proof-of-concept for an on-call assistant that pulls data from:
- **Grafana MCP**: returns health snapshots for services (mocked with JSON).
- **FastAPI MCP (on-call)**: lists/acks incidents from a simple fixture.
- **Chat surface**: browser UI that shows how a Dify LLM/chat mode can call the MCPs to answer on-duty questions.

The goal is to demonstrate the wiring; swap the mocked data sources with real endpoints when ready.

### Run locally
1) Install Python 3.11+ and create a venv (recommended).
2) With `pip`: `pip install -r requirements.txt`  
   With `uv`: `uv venv .venv && source .venv/bin/activate && uv pip install -r requirements.txt`
3) `uvicorn app.main:app --reload`
4) Open http://127.0.0.1:8000 and ask things like “check latency for orders” or “any incidents for payments”.

Or use the helper script:
- Single process (defaults to `all` on port 8000): `./start.sh [all|web|grafana|oncall|k8s]`
- Multi-process (separate ports 8000/8011/8012/8013 by default): `./start.sh multi`

Run components separately (simulate different servers):
- Grafana MCP only: `APP_MODE=grafana HOST=127.0.0.1 PORT=8011 ./start.sh`
- On-call MCP only: `APP_MODE=oncall HOST=127.0.0.1 PORT=8012 ./start.sh`
- K8s MCP only: `APP_MODE=k8s HOST=127.0.0.1 PORT=8013 ./start.sh`
- Web chat only: `APP_MODE=web HOST=127.0.0.1 PORT=8000 GRAFANA_MCP_URL=http://127.0.0.1:8011/api/mcp/grafana ONCALL_MCP_URL=http://127.0.0.1:8012/api/mcp/oncall K8S_MCP_URL=http://127.0.0.1:8013/api/mcp/k8s ./start.sh`
- Start all four at once: `PROFILE=multi ./start.sh` (uses ports 8000/8011/8012/8013 by default; override with *_HOST/PORT vars)

LLM (OpenAI-compatible) config for the chat endpoint:
- `OPENAI_API_KEY` (required)  
- `OPENAI_MODEL` (optional, default `gpt-4o-mini`)  
- `OPENAI_BASE_URL` (optional; use when pointing to a self-hosted OpenAI-compatible endpoint)
- When web runs separately from MCPs, point tools at remote servers:  
  - `GRAFANA_MCP_URL` (e.g., `http://127.0.0.1:8011/api/mcp/grafana`)  
  - `ONCALL_MCP_URL` (e.g., `http://127.0.0.1:8012/api/mcp/oncall`)  
  - `K8S_MCP_URL` (e.g., `http://127.0.0.1:8013/api/mcp/k8s`)
  - `MCP_HTTP_TIMEOUT` (optional, default 8s)

Web chat endpoints:
- `/api/web/chat` (rule-based)
- `/api/web/chat-llm` (LLM with tool-calling to MCPs)

MCP endpoints you can register as tools:
- Grafana MCP: `GET /api/mcp/grafana/services`, `GET /api/mcp/grafana/{service}`
- On-call MCP: `GET /api/mcp/oncall/incidents?service=...`, `POST /api/mcp/oncall/incidents/ack`
- K8s MCP: `GET /api/mcp/k8s/overview`, `GET /api/mcp/k8s/pods?namespace=...`, `POST /api/mcp/k8s/pods/restart`
Legacy frontend paths still work: `/api/services`, `/api/grafana/{service}`, `/api/incidents`, `/api/incidents/ack`.

Process modes (set `APP_MODE`):  
- `all` (default) serves everything; `web` serves chat + legacy; `grafana`, `oncall`, `k8s` expose only those MCPs.

### What’s inside
- `app/main.py`: FastAPI app exposing web chat under `/api/web/*` (rule-based `/chat`, LLM `/chat-llm`), Grafana MCP under `/api/mcp/grafana/*`, On-call MCP under `/api/mcp/oncall/*`, K8s MCP under `/api/mcp/k8s/*`, plus legacy endpoints used by the frontend (`/api/services`, `/api/grafana/{service}`, `/api/incidents`, `/api/incidents/ack`). Serves the frontend.
- `app/mcp/grafana.py`: Grafana MCP facade reading `data/metrics.json`.
- `app/mcp/oncall.py`: On-call MCP facade reading/writing `data/incidents.json` (acks persist).
- `app/mcp/k8s.py`: K8s MCP facade reading/writing `data/k8s.json` (pod restarts persist).
- `app/orchestrator.py`: Minimal “agent” that calls both MCPs and composes a response.
- `static/`: HTML/CSS/JS chat UI.
- `data/`: Fixture data for metrics, incidents, and k8s cluster state.

### Using with Dify (LLM chat mode)
This PoC surfaces HTTP endpoints you can register as tools in a Dify workflow:
- Tool 1 (Grafana MCP): `GET /api/grafana/{service}` → returns latency/error/CPU/RPS + Grafana panel link.
- Tool 2 (On-call MCP): `GET /api/incidents?service=orders-service` → active incidents; `POST /api/incidents/ack` to acknowledge.
- Tool 3 (Orchestrator): `POST /api/chat` → single-call helper that already chains the two MCPs.

Suggested Dify setup:
1. In your Dify app, add HTTP tools for the endpoints above with brief descriptions (“Fetch health from Grafana MCP”, “List active incidents”, “Acknowledge an incident”).
2. In the chat flow, allow the model to call those tools; seed the system prompt with service names from `/api/services` and remind it to pick the closest match (e.g., “orders” → “orders-service”).
3. Replace the JSON fixtures with real data (update `data/metrics.json` and `data/incidents.json`, or point the MCP facades to real APIs).

### Customizing the PoC
- Add more services or metrics in `data/metrics.json`.
- Preload more incidents or tweak severities in `data/incidents.json`.
- Extend `app/orchestrator.py` to include more signals (logs, release info, SLOs) or to call the tools differently.
